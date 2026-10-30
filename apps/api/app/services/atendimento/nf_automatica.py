"""A NF pelo agente, SOZINHA, nas lojas no automático (28/10/2026).

O Eduardo, ao ver o anexo da NF pela tela: "liga sim fechou so para lojas
automaticas ele consegue enviar por ele mesmo a nf na conversa". As condições
aprovadas, TODAS juntas (qualquer uma que falte → fica com a pessoa, com o
"Anexar a NF" do cartão da sugestão, como antes):

1. Qualquer canal em modo `auto` com o agente ligado — sem lista de lojas
   (complemento do Eduardo: "se eu ativar outra loja ela já deve poder enviar
   a nf automaticamente também") —, nas caixas que recebem o anexo (`CAIXAS`:
   chat da Shopee e do TikTok como imagem, pós-venda do ML como PDF; Amazon e
   Magalu fora até terem envio de anexo) e com o assunto `nota_fiscal` MARCADO
   na loja, como todo assunto do automático (`auto_categorias` e
   `auto_agente` do canal — é o que marcar ao ligar uma loja nova).
2. O cliente pediu a NF de forma CLARA (`pedido_claro`): verbo de pedido ou
   "não veio a nota", sem CNPJ/XML/correção/reemissão, sem outro assunto no
   turno (o rascunho do agente só com a NF: `elegivel`). Ambíguo → pessoa.
3. A nota CERTA (`conferir_nf`): a cheia, autorizada, de saída normal, do
   pedido DESTA conversa (o `numeroloja` do Bling É o pedido da conversa), o
   pedido é DESTE comprador — conferido AO VIVO na plataforma na hora do envio
   (`pedido_na_plataforma`: Shopee `buyer_user_id`, TikTok `user_id` = o
   comprador do chat; no ML a conversa pós-venda É a do pacote do pedido) e,
   na Shopee, também no índice de pedidos —, o destinatário da NF é o do
   pedido (CPF/CNPJ) e a chave bate com o número, o emitente e o XML. LGPD: a
   DANFE tem nome, endereço e CPF — nunca a nota de outro cliente.
4. Pedido cancelado, carrinho com vários pedidos, só a NF de embalagem (a
   cheia sai no lote da noite) e comprador com MAIS DE UM pedido (na loja, pelo
   índice da Shopee, ou nos cartões de pedido da conversa: qual NF ele quer?)
   → pessoa.
5. As travas do envio sozinho: o automático ligado, o modo `auto`, o agente
   ligado no canal, a conversa aguardando (não pausada, atribuída, fechada,
   bloqueada, sem reclamação), a pausa humana de 30 min, a triagem da Caixa
   Humano (só a ação "Enviar o arquivo da NF" é liberada — qualquer outro
   motivo segura) e, no envio, a trava da conversa e o gatilho ainda sem
   resposta (`enviar.enviar_nf_automatica`). O verificador semântico NÃO
   roda aqui: ele confere o TEXTO que o modelo escreveu, e o que sai é a frase
   fixa `LEGENDA` com o documento que o CÓDIGO escolheu — as conferências
   acima fazem o papel dele, todas determinísticas.
6. Na Shopee e no TikTok a NF vai como IMAGEM da DANFE (o chat não aceita
   arquivo) e, depois, a frase `LEGENDA`; no pós-venda do ML, o PDF junto da
   frase (o ML manda o anexo dentro de uma mensagem com texto).

NUNCA DUAS VEZES na mesma conversa: a mensagem leva `payload.nf.chave`; antes
de sair (e de novo sob a trava da conversa) qualquer mensagem da conversa com
a mesma chave ou a mesma imagem (a DANFE é determinística: o mesmo XML dá os
mesmos bytes, inclusive a mandada pela pessoa no botão "NF") segura.

Chave geral: `ATENDIMENTO_NF_AUTOMATICA` (padrão ligado; desligada, tudo fica
com a pessoa). Só ids e códigos no log, nunca dado do cliente.

REVISÃO DE 29/10/2026 (os casos reais de 28/10 nas lojas no automático):

- O pedido claro (item 2) aceita o DOCUMENTO que faltou na caixa — "Não veio
  nota fiscal..nem recibo nada documento" (Barbosa e5fe56d4, `nf:pedido_nao_
  claro`): o "mas" de "o pedido chegou mas não veio a nota fiscal" passa, e
  "nem", "nada", "nenhum", "ou" e "também" só GRUDADOS no papel ("nem recibo",
  "nada documento", "nota fiscal nenhuma", "ou recibo", "também a nota":
  `_GRUDADO_NO_PAPEL`). O PRODUTO que não veio continua com a pessoa: o
  conectivo solto sobra na lista ("nem chegou", "nada do produto", "o produto
  também não", "a nota ou o produto"), a regra explícita do `_NAO_E_SO_A_NF`
  ("não veio nada", "não chegou o pedido", "o pacote não chegou" — antes "não
  recebi o pedido, cadê a nota" passava pela lista) e o trecho com "não" sem
  o documento nele ("ainda não chegou, preciso da nota", "não veio a nota, o
  produto não", `_NAO_VEIO`).
- Segunda revisão de segurança de 29/10 (frases escritas à mão que a lista
  deixava passar): o recibo/documento/comprovante SÓ grudado no "nem/nada/
  ou" ("vou mandar o documento pra nota" é a NF com outro documento → pessoa);
  o "mas" só antes do "não" ou do "na caixa" ("chegou mas não veio a nota";
  "mas e o pedido?", "mas a nota não é minha", "mas do pedido certo"
  sobram); cada FALA é um trecho ("Não veio a nota || O produto não"), e o
  pedido sem vírgula também ("ainda não chegou preciso da nota"); e no
  `_NAO_E_SO_A_NF` o produto depois do "e" ("não veio a nota e o produto"),
  o produto pedido ("quero meu produto e a nota", "cadê o pedido"), o
  produto com "não" ("o produto não estava na caixa" — "a caixa não tinha a
  nota" continua pedido da NF), a nota que "não é" a dele, o "pedido/nota
  certo", o "não quero" e outra nota ("a segunda nota", "a nota de um
  produto", "e também a nota do pedido"). Medido nos turnos reais de 60 dias
  (até 29/10, 708 turnos e 424 parciais que citam a nota): passam 26 turnos e
  10 parciais (antes 27 e 10) — o que deixou de passar é "eu queria a nota
  fiscal || não veio".
- O "2" do menu (`pedido_pelo_menu`) olha também o que o cliente escreveu
  ANTES do menu na mesma rajada (o menu sai logo depois da primeira fala:
  "recebi a nota de outra pessoa" → menu → "2" passava): só cortesia, o "2"
  de um menu anterior ou o próprio pedido claro da NF. É a régua do pedido
  claro, e custa: nos 60 dias, dos 155 "2" do menu passam 24 (nas lojas no
  automático, 46 → 13). A régua frouxa (a nota nomeada e sem as travas do
  `_NAO_E_SO_A_NF`) ficaria com 93, mas deixava passar pergunta de pré-venda
  e outro assunto antes do menu ("faz envio para … e faz a nota?", "é
  original? não veio a nota", "conversar com vendedor") — fica com a pessoa.
- A CHAVE DE ACESSO da nota é pedido da NF (Barbosa 75fd5e5d, 17:03: "pode
  mandar a chave de acesso para eu emitir a nota fiscal"): a DANFE traz a
  chave impressa e no código de barras. Só a expressão inteira, sem
  complemento ou "da nota", e com a nota nomeada no turno — a "chave"
  sozinha (a que não veio na caixa) e a "chave de acesso do produto/do
  aplicativo" sobram; "para eu emitir/imprimir/baixar/consultar" é o que o
  cliente faz com ela e não sobra. Medido nos turnos reais de 30 dias (até
  29/10, 371 turnos e 743 falas parciais que citam a nota): passam 11 turnos
  e 26 parciais (antes 9 e 23), os 5 a mais são pedidos simples da NF e
  nenhum que passava deixou de passar.
- A NF que ainda não está no sistema (`nf_nao_chegou`, `nf_so_embalagem`)
  continua com a pessoa: o texto padrão da equipe promete "enviaremos a nota
  em até 1 dia útil" (o validador barra prazo vindo da IA) e cumprir a
  promessa pede um caminho novo — mandar a NF na conversa já respondida e
  devolver à Caixa Humano depois do prazo. Medido em produção (30 dias até
  29/10): dos 67 pedidos claros da NF (56 pelo "2" do menu), 28 já tinham a
  cheia no DaVinci, 23 nunca a receberam no `nf_nota` (só a de embalagem, ou
  pedido de antes do coletor — entregues) e só 3, em lojas fora do automático,
  eram o "ainda não emitida" normal; depois do texto padrão a equipe manda
  anexo em 60% (39% em 1 dia). Prometer seria atrasar a pessoa em 1 dia útil
  sem cumprir: fica com a pessoa. O caso Vita 95704e8d
  (`nf:checagens`) é outro: o agente deu confiança baixa porque o pedido é de
  07/08 (Entregue) e não tem NF nenhuma no `nf_nota` (o coletor sobe desde
  19/08) — entregue e sem NF é anormal: pessoa.

REVISÃO DE 30/10/2026 (o Eduardo: "faça esse ajuste para termos mais entregas de
NF automaticamente quando é requisitado"; em 29/10 ficaram sem resposta, como
`nf:pedido_nao_claro`, o Barbosa e12c7799 "a notafiscal?" e o Vita 95704e8d "O
meu celular não veio com nota fiscal"):

- O pedido claro lê a GRAFIA do documento ("notafiscal", "nota fical", "danfe",
  "cupom fiscal", "nf-e": `_grafia`), o documento SOZINHO no turno ("nota
  fiscal", "a nf do pedido?"), a AUSÊNCIA dita de outro jeito ("não veio com a
  nota", "veio/chegou sem nota", "faltou a nota"), o APARELHO como o produto
  (com as mesmas travas: "não veio o celular", "o celular não chegou, manda a
  nota" e o acessório — "não veio o carregador nem a nota" — ficam com a
  pessoa), o "como faço para receber a nota", a cortesia que segurava ("por
  gentileza", "muito obrigado", "fico no aguardo") e o "que/só/mesmo".
- O "2" que escolheu "Nota fiscal" no menu sai das falas do turno
  (`textos_do_turno`): o pedido escrito junto passa; o resto do turno, inclusive
  o que veio antes do menu, continua na mesma régua.
- Medido nos turnos reais de 60 dias (até 30/10, todas as lojas; 378 turnos e
  759 parciais que citam o documento): passam 42 turnos e 84 parciais (a
  revisão de 29/10 sozinha: 18 e 34; o código no ar: 62 e 95, mas 53 e 71 deles
  só pelo "2" do menu sem olhar o que veio antes). Conversas em que a NF certa
  sairia (a cheia única, o CPF do pedido, o comprador conferido): 43, 20 nas
  lojas no automático (29/10: 21 e 10; no ar: 57 e 22). Das 42 conversas novas,
  27 têm a NF certa (15 no automático); lidas todas, 1 era pergunta de
  pré-venda ("vc manda a nota fiscal do aparelho?", sem pedido: não sai). O que
  o código no ar mandava e continua com a pessoa é o "2" com texto antes que não
  é pedido claro (pré-venda, "é original?", série na nota, e-mail e alguns
  pedidos com palavra fora da lista).
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    AtendimentoCanal,
    AtendimentoConversa,
    AtendimentoMensagem,
    AtendimentoPedidoComprador,
    AtendimentoRascunho,
    BlingOrder,
    NfNota,
)
from app.services.atendimento import constantes, danfe, validador

logger = structlog.get_logger()

LEGENDA = "Segue a nota fiscal do seu pedido."
CATEGORIA = "nota_fiscal"
# As caixas que recebem o anexo (`foto.CAIXAS_COM_FOTO`, sem o Zap) e onde o
# comprador do pedido é conferido (`pedido_na_plataforma`).
CAIXAS = frozenset(
    {
        ("shopee", constantes.CANAL_CHAT),
        ("tiktok", constantes.CANAL_CHAT),
        ("ml", constantes.CANAL_POS_VENDA),
    }
)
# Pedidos do mesmo comprador na loja nesta janela: mais de um = qual NF? → pessoa.
JANELA_PEDIDOS = timedelta(days=60)
# O motivo que o agente grava no `sem_auto` para a ação da NF (o único aceito).
MOTIVO_SEM_AUTO_NF = (
    f"ação de pessoa: {constantes.rotulo_acao_pessoa(constantes.ACAO_ENVIAR_ARQUIVO_NF)}"
)


class NaoSai(Exception):  # noqa: N818 — nome do domínio
    """A NF não sai sozinha; `code` vai para o registro do automático (`nf:<code>`)."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


# ── O pedido claro (PURA) ─────────────────────────────────────────────────

# Revisão de 29/10 (Barbosa 75fd5e5d, "pode mandar a chave de acesso para eu emitir a nota
# fiscal"): a chave de acesso está na DANFE (impressa e no código de barras) — pedir a chave
# DA NOTA é pedir a NF. Só com a nota nomeada no turno (`_NOMEIA_A_NF`): "a chave de
# acesso" sozinha pode ser a do aplicativo/produto.
_DOC = r"(?:nota(?:\s+fiscal)?|nf-?e?|nfe|chave\s+de\s+acesso)"
_NOMEIA_A_NF = re.compile(r"\b(?:nota|notas|nf|nfe)\b")
# O que o cliente faz com a NF ("para eu emitir/imprimir/baixar/consultar"): não sobra. O
# "emitir" só com o "eu" ("manda a nota pra emitir" pode ser a loja emitir outra nota).
_O_CLIENTE_FAZ = re.compile(
    r"\b(?:para|pra)\s+(?:eu\s+emitir|(?:eu\s+)?(?:imprimir|baixar|consultar))\b"
    # Revisão de 30/10: "(e) preciso dela", "vou precisar dela" — a dela é a nota do turno ("a
    # nota dela", de outra pessoa, continua fora da lista).
    r"|\b(?:preciso|precisava|precisaria|vou\s+precisar)\s+dela\b"
)
# Revisão de 30/10: "como faço para receber/ter acesso à nota fiscal?", "como consigo a nf?" —
# o que se responde é a NF. O "como" só nesta forma, grudado no documento ("como faço para
# trocar", "como consigo a nota no cnpj" seguem com a pessoa pelas outras réguas).
_COMO_RECEBO = re.compile(
    r"\bcomo\s+(?:eu\s+)?(?:faco|consigo|posso)\s+(?:(?:pra|para)\s+(?:eu\s+)?)?"
    r"(?:(?:ter\s+acesso|acessar|receber|pegar|conseguir|obter)\s+)?"
    r"(?=(?:(?:a|da|minha)\s+)?(?:nota|nf|nfe)\b)"
)
# O papel que faltou na caixa.
_PAPEL = r"(?:nota|notas|nf|nfe|recibo|recibos|documento|documentos|comprovante|comprovantes)"
# Revisão de segurança de 29/10: o que SÓ vale grudado no papel — o "nem/nada/nenhum/ou/
# também" ("nem recibo", "nada documento", "nenhuma nota", "nota fiscal nenhuma", "ou
# recibo", "também a nota") e a "chave de acesso" sem complemento ou "da nota" (a "chave"
# sozinha e a "chave de acesso do produto" sobram). Solto, o conectivo é o PRODUTO que não
# veio: "nem chegou", "nada do produto", "o produto também não", "a nota ou o produto".
_GRUDADO_NO_PAPEL = re.compile(
    r"\b(?:nem|nada|nenhum|nenhuma|ou|tambem)\s+(?:d[aeo]s?\s+)?(?:(?:o|a|os|as|um|uma)\s+)?"
    + _PAPEL
    + r"\b|\b"
    + _PAPEL
    + r"(?:\s+fiscal)?\s+nenhuma?\b"
    r"|\bchave\s+de\s+acesso\b(?:\s+d[aeo]\s+(?:nota|nf|nfe)\b)?(?!\s+(?:d[aeo]s?|nem)\b)"
)
# O que veio (ou não) na entrega — o produto, não o documento.
_O_PRODUTO = r"(?:nada|produto|produtos|pedido|compra|caixa|pacote|encomenda|embalagem)"
# "(a caixa não) tinha/tem/estava a nota": o que faltou no produto é o documento. Revisão de
# 30/10: "(o pedido não) veio a nota" também — é o "não veio COM a nota" / "veio SEM a nota"
# depois do `_grafia` (sem o "nem": "o produto não veio nem a nota" é o produto → pessoa).
_FALTOU_O_PAPEL = (
    r"(?:\s+(?:tinha|tem|estava)\s+(?:(?:a|as|o|os|nem)\s+)?" + _PAPEL + r"\b"
    r"|\s+(?:veio|vieram)\s+(?:(?:a|as|o|os)\s+)?(?:nota|nf|nfe)\b)"
)
_PEDE = re.compile(
    # "me manda a nota", "pode enviar a nf?", "passa a nota fiscal", "encaminha a nota" (30/10)
    r"\b(?:manda|mande|mandar|mandem|envia|envie|enviar|enviem|passa|passe|passar|"
    r"encaminha|encaminhe|encaminhar|encaminhem)\b"
    r"(?:\s+\w+){0,3}?\s+(?:a\s+|minha\s+|sua\s+)?" + _DOC + r"\b"
    # "preciso da nota fiscal", "queria a nf do pedido"
    r"|\b(?:preciso|precisava|precisaria|quero|queria|gostaria|necessito)\b"
    r"(?:\s+\w+){0,3}?\s+(?:a\s+|da\s+|de\s+|uma\s+)?" + _DOC + r"\b"
    # "não veio a nota", "a nota fiscal não chegou", "cadê a nf"
    r"|\bnao\s+(?:veio|vieram|recebi|chegou|encontrei|achei|tinha|tem|estava)\b(?:\s+\w+){0,4}?\s+"
    r"(?:a\s+)?" + _DOC + r"\b"
    r"|\b" + _DOC + r"\b(?:\s+\w+){0,3}?\s+nao\s+(?:veio|chegou|estava|tinha|foi)\b"
    r"|\bcade\s+(?:a\s+)?(?:minha\s+)?" + _DOC + r"\b"
    # "vou querer a nota", "quero receber a nf", "solicito a nota fiscal"
    r"|\b(?:querer|receber|solicit\w*)\b(?:\s+\w+){0,3}?\s+(?:a\s+|da\s+|de\s+)?" + _DOC + r"\b"
)
# Pedido que NÃO é "mande a NF do pedido": CNPJ, XML, correção, reemissão,
# série/IMEI, nota de outro, ou outro assunto colado (dinheiro, devolução).
# Revisão de 30/10: a "danfe" saiu daqui — ela é a NF que sai (o `_grafia` a lê como "nota
# fiscal"); com o XML, o CNPJ ou a correção, continua com a pessoa por eles.
_NAO_E_SO_A_NF = re.compile(
    r"\b(?:cnpj|inscricao\s+estadual|razao\s+social|empresa|xml|"
    r"corrig\w*|correc\w*|reemit\w*|reemiss\w*|errad\w*|incorret\w*|alter\w*|troc\w*|"
    r"outro\s+nome|outro\s+cpf|outra\s+pessoa|serie|imei|"
    r"cancel\w*|devol\w*|reembols\w*|estorn\w*|defeito|quebrad\w*|reclam\w*|procon|"
    r"garantia|mandam|enviam|emitem|emite|acompanha\w*|vem\s+com)\b"
    r"|\bja\s+(?:recebi|tenho|veio|chegou)\s+(?:a\s+)?" + _DOC + r"\b"
    r"|\bnao\s+precis\w*\b"
    # Revisão de 29/10: o PRODUTO que não veio ("não veio nada", "não chegou o pedido",
    # "o pacote não chegou") é reclamação, não pedido da NF — mesmo com as palavras da
    # lista ("nem o produto" e "nenhum produto" sobram: o "nem/nenhum" só vale grudado no
    # papel, `_GRUDADO_NO_PAPEL`). "A nota DO pedido não veio" e "NA caixa não veio"
    # continuam pedido da NF (o produto ali é complemento).
    r"|\bnao\s+(?:veio|vieram|chegou|chegaram|recebi|tinha|tem|estava|foi)\s+"
    r"(?:(?:o|a|os|as|meu|minha|nem|ainda)\s+){0,2}" + _O_PRODUTO + r"\b"
    # Segunda revisão de 29/10: o produto com "não" ("o produto não estava na caixa", "não
    # veio a nota o produto não") — só "a caixa não tinha/tem/estava a nota" é o documento.
    r"|(?<![dn][aoe] )(?<![dn][ao]s )\b" + _O_PRODUTO + r"\s+(?:ainda\s+|tambem\s+)?nao\b"
    r"(?!"
    + _FALTOU_O_PAPEL
    + r")"
    # O produto depois do "e" ("não veio a nota e o produto", "manda a nota e o pedido",
    # "chegou mas não veio a nota e um produto", "cadê a nota e cadê o pedido").
    + r"|\be\s+(?:cade\s+)?(?:(?:o|a|os|as|meu|minha|um|uma)\s+){0,2}"
    + _O_PRODUTO
    + r"\b(?!\s+nao"
    + _FALTOU_O_PAPEL
    + r")"
    # O produto pedido ("quero meu produto e a nota", "me envia o pedido e a nota", "cadê o
    # pedido e a nota").
    + r"|\b(?:manda|mande|mandar|mandem|envia|envie|enviar|enviem|passa|passe|passar|encaminh\w*|"
    r"preciso|precisava|precisaria|quero|queria|gostaria|necessito|querer|receber|solicit\w*)\s+"
    r"(?:(?:o|a|os|as|meu|minha|do|da|dos|das|de|um|uma)\s+){0,2}" + _O_PRODUTO + r"\b"
    r"|\bcade\s+(?:(?:o|a|os|as|meu|minha)\s+){0,2}" + _O_PRODUTO + r"\b"
    # A nota que não é a dele, o pedido/nota "certo" e o "não quero" ("essa nota não é
    # minha", "manda a nota do pedido certo", "não quero a nota").
    r"|\bnao\s+e\b|\b(?:pedido|nota)\s+cert[oa]\b"
    r"|\bnao\s+(?:quero|queria|gostaria|vou\s+querer)\b"
    # Outra nota além da deste pedido ("e também a nota do pedido", "a segunda nota", "a
    # nota de um produto") — a "segunda via" é a mesma nota.
    r"|\btambem\s+(?:a\s+|as\s+)?(?:nota|notas|nf|nfe)(?:\s+fiscal)?\s+d[aeo]s?\b"
    r"|\bsegunda\s+(?!via\b)\w"
    r"|\bd[aeo]\s+(?:um|uma)\s+(?:dos\s+|das\s+)?" + _O_PRODUTO + r"\b"
)


# Revisão de 28/10: a régua é a LISTA DO QUE PODE — tirando o pedido da NF e a
# cortesia, nada pode sobrar ("no meu cpf", "no nome da minha esposa", "do
# outro pedido", "faltando o cabo" vão para a pessoa). Número (CPF, pedido)
# também não: a palavra com dígito não está na lista. Revisão de 29/10 (Barbosa,
# "Não veio nota fiscal..nem recibo nada documento"): o documento que faltou na
# caixa entra SÓ grudado no papel — recibo, documento, comprovante, "nem", "nada",
# "nenhum", "ou", "também" e a "chave de acesso" (`_GRUDADO_NO_PAPEL`; solto, "vou
# mandar o documento pra nota" é a NF com outro documento) —, e o "mas" só antes do
# "não" (`_MAS_NAO`); o produto que não veio fica com a pessoa pelo `_NAO_E_SO_A_NF`
# e pelo trecho (`_NAO_VEIO`). Revisão de 30/10: a cortesia que segurava pedido simples nos
# 60 dias ("por gentileza", "muito obrigado", "fico no aguardo") e o "que/q",
# o "só", o "mesmo" e o pedido no subjuntivo ("gostaria que me enviasse a nota fiscal", "só não
# veio a nota", "pode enviar por aqui mesmo") — o "dela" não ("a nota dela" é de outra pessoa).
_CORTESIA = frozenset(
    """
    oi ola ei bom boa dia tarde noite tudo bem ok certo sim por favor pf pfv porfavor
    obrigado obrigada obg grato grata agradeco desde ja gentileza muito aguardo fico
    """.split()
)
_PALAVRAS_DO_PEDIDO = _CORTESIA | frozenset(
    """
    pode poderia podem poderiam consegue conseguem conseguiria tem como teria seria possivel
    voce voces vc vcs me mim pra para pro por aqui ai la
    manda mande mandar mandem envia envie enviar enviem passa passe passar
    encaminha encaminhe encaminhar encaminhem
    preciso precisava precisaria quero queria gostaria necessito eu vou querer receber
    solicito solicitar solicitacao que q so mesmo enviasse enviassem mandasse mandassem
    nao veio vieram recebi chegou encontrei achei tinha estava foi cade ainda
    nota notas fiscal nf nfe copia arquivo pdf digital via segunda de novo
    a o as os um uma da do de na no em e minha meu sua seu desse deste dessa desta
    esse este essa esta junto caixa pacote embalagem pedido compra produto
    """.split()
)


# O menu do robô da loja (o do Duoke, 28/10/2026): "Escolha uma opção: 1 - Rastreio
# 2 - Nota fiscal …" — a resposta "2" é o pedido da NF.
_MENU_NF = re.compile(r"\b2\s*[-–.)]\s*nota\s+fiscal\b")


def pedido_pelo_menu(recentes: Sequence[Any], gatilho: Any) -> bool:
    """O cliente respondeu SÓ "2" depois do menu da loja em que o 2 é "Nota fiscal"? PURA.

    O que vale é o que o cliente escreveu DEPOIS do último menu (o menu do robô
    não conta como resposta da loja, então a rajada pega o "oi" de antes), e o
    gatilho do rascunho está entre essas falas. Segunda revisão de 29/10: o menu
    sai logo depois da PRIMEIRA fala do cliente, então o que ele escreveu ANTES do
    menu na mesma rajada ("recebi a nota de outra pessoa" → menu → "2") também
    conta: só cortesia, o "2" de um menu anterior ou o próprio pedido claro da NF
    (a foto sem texto não conta).
    """
    from app.services.atendimento import ia

    ultimo = max(
        (
            i
            for i, m in enumerate(recentes)
            if m.autor == constantes.AUTOR_LOJA
            and _MENU_NF.search(validador.plano_de(m.texto or ""))
        ),
        default=None,
    )
    if ultimo is None:
        return False
    depois = [m for m in recentes[ultimo + 1 :] if m.autor == constantes.AUTOR_CLIENTE]
    if not depois or gatilho is None or all(m.id != gatilho.id for m in depois):
        return False
    plano = " ".join(validador.plano_de(" ".join(m.texto or "" for m in depois)).split())
    if plano.strip(" .!") != "2":
        return False
    antes = [
        t
        for t in (m.texto for m in ia.rajada_do_cliente(recentes[:ultimo], None))
        if t and validador.plano_de(t).strip(" .!\n") != "2"
    ]
    return all(_so_cortesia(t) for t in antes) or pedido_claro(antes)


def textos_do_turno(recentes: Sequence[Any], rajada: Sequence[Any]) -> list[str | None]:
    """As falas do turno para o `pedido_claro`, sem o "2" que escolheu "Nota fiscal". PURA.

    Revisão de 30/10/2026: o "2" depois do menu da loja (o mesmo de `pedido_pelo_menu`) É o
    pedido da NF, mas contado como palavra ele segurava o pedido escrito no mesmo turno ("me
    envie a nota fiscal || 2 || me envie a nota fiscal por aqui"; em 60 dias, 16 decisões, 12
    em lojas no automático). Só sai o "2" que vem depois do menu e antes de uma resposta real
    da loja; o resto do turno passa pela MESMA régua — o que veio antes do menu também ("vem
    com nota fiscal? || 2", "conversar com vendedor || 2 || …" continuam com a pessoa) — e os
    outros números do menu (3 cancelamento, 5 garantia, 6 atendente) seguram.
    """
    from app.services.atendimento import ia

    posicao = {m.id: i for i, m in enumerate(recentes)}
    menus = [
        i
        for i, m in enumerate(recentes)
        if m.autor == constantes.AUTOR_LOJA and _MENU_NF.search(validador.plano_de(m.texto or ""))
    ]
    respostas = [i for i, m in enumerate(recentes) if ia.resposta_da_loja(m)]

    def escolheu_a_nf(m: Any) -> bool:
        i = posicao.get(m.id)
        if i is None or validador.plano_de(m.texto or "").strip(" .!\n") != "2":
            return False
        resposta = max((j for j in respostas if j < i), default=-1)
        return any(resposta < j < i for j in menus)

    return [m.texto for m in rajada if not escolheu_a_nf(m)]


def _so_cortesia(texto: str) -> bool:
    """Só cumprimento/agradecimento ("oi", "boa tarde, tudo bem?", "por favor")? PURA.

    O "6" do menu ("Falar com Atendente") não é cortesia aqui: ele pediu a pessoa.
    """
    from app.services.atendimento import ia

    palavras = re.findall(r"[a-z0-9]+", validador.plano_de(texto))
    if "6" in palavras or "seis" in palavras:
        return False
    return all(p in _CORTESIA for p in palavras) or ia.mensagem_de_continuidade(texto)


# Revisão de 29/10: o "não" de cada trecho fala do DOCUMENTO; sem ele no trecho ("ainda não
# chegou, preciso da nota", "manda a nota mas ainda não chegou", e o verbo subentendido de
# "não veio a nota, o produto também não") quem não veio é o produto → pessoa. O trecho
# acaba na pontuação, no "mas", "porém" e "e"; a "chave" sozinha não é o documento.
# Segunda revisão de 29/10: cada FALA também é um trecho ("Não veio a nota || O produto
# não", "Ainda não chegou || Manda a nota") — o "eu queria a nota || não veio" fica com a
# pessoa (1 dos 27 turnos reais de 60 dias que passavam) — e o pedido que começa outra
# frase sem a vírgula também ("ainda não chegou preciso da nota").
_NAO_VEIO = re.compile(r"\bnao\b")
_TRECHOS = re.compile(
    r"[,.;:!?]+|\b(?:mas|porem|e)\b"
    r"|(?=\b(?:preciso|precisava|quero|queria|gostaria|cade|manda|mande|envia|envie)\b)"
)
_NOME_DO_DOC = re.compile(r"\b" + _PAPEL + r"\b")
# O "mas" só vale antes do "não" e do lugar ("o pedido chegou mas não veio a nota", "mas na
# caixa não tinha a nota"); "mas e o pedido?", "mas a nota não é minha" sobram. Revisão de
# 30/10: o "porém" é o mesmo "mas" ("o aparelho chegou, porém não veio a nota fiscal").
_MAS_NAO = re.compile(r"\b(?:mas|porem)\s+(?=nao\b|n[ao]s?\s+(?:caixa|embalagem|pacote)\b)")

# Revisão de 30/10/2026 (o Eduardo: "faça esse ajuste para termos mais entregas de NF
# automaticamente quando é requisitado"; nas lojas no automático, em 29/10, ficaram sem
# resposta como `nf:pedido_nao_claro` o Barbosa e12c7799 "a notafiscal?" e o Vita 95704e8d
# "O meu celular não veio com nota fiscal"). Antes das réguas, o `_grafia` reescreve:
# - a GRAFIA do documento: grudada ou com o "fiscal" errado ("notafiscal", "nota fical",
#   "notafical", "nota fiscall", "nota física"), a "danfe" e o "cupom fiscal" viram "nota
#   fiscal"; "nf-e" vira "nfe". O "cupom" sozinho (o de desconto), o "nfc" (o do celular) e as
#   "notas fiscais" (mais de uma: qual?) ficam como estão;
_GRAFIAS = (
    (re.compile(r"\bnota\s*(?:fiscal|fiscall|fical|fisca|fisical|fisica)\b"), "nota fiscal"),
    (re.compile(r"\b(?:danfe|cupom\s+fiscal)\b"), "nota fiscal"),
    (re.compile(r"\bnf\s*-\s*e\b"), "nfe"),
)
# - o APARELHO é o produto ("o meu celular não veio com nota" = "o produto não veio com a
#   nota"): vira "produto" e passa pelas MESMAS travas do produto — "não veio o celular",
#   "o celular não chegou, manda a nota", "quero o celular e a nota" continuam com a pessoa. O
#   acessório (carregador, cabo, fone, capinha) não entra: fica fora da lista do que pode e
#   "não veio o carregador nem a nota" vai para a pessoa;
_O_APARELHO = re.compile(r"\b(?:celular|aparelho|telefone|smartphone|relogio|smartwatch)\b")
# - a AUSÊNCIA dita de outro jeito vira o "não veio a nota" que as réguas já leem: "não veio
#   COM a nota" (o "com" só depois do "não": "veio com a nota?" e "vem com nota?" ficam) e
#   "veio/chegou/recebi SEM a nota" (o "sem" só entre o verbo e a nota: "pode mandar sem nota",
#   "veio sem caixa e sem nota" e "veio sem nota e sem carregador" sobram na lista) e "faltou /
#   ficou faltando a nota" (só com a nota logo depois: "faltou o carregador e a nota" sobra).
#   Negado ("não veio sem nota", "não está faltando a nota") não é a ausência: fica como está.
_ANTES_DA_NOTA = r"(?=(?:(?:a|as|o|os|uma)\s+)?(?:nota|nf|nfe)\b)"
_NAO_ANTES = r"(?<!nao )(?<!nao veio )(?<!nao esta )(?<!nao estava )(?<!nao ficou )"
_SEM_A_NOTA = (
    (re.compile(r"\bnao\s+(?:veio|vieram|chegou|chegaram)\s+com\s+" + _ANTES_DA_NOTA), "nao veio "),
    (
        re.compile(_NAO_ANTES + r"\b(?:veio|vieram|chegou|chegaram)\s+sem\s+" + _ANTES_DA_NOTA),
        "nao veio ",
    ),
    (re.compile(_NAO_ANTES + r"\brecebi\s+sem\s+" + _ANTES_DA_NOTA), "nao recebi "),
    (
        re.compile(
            _NAO_ANTES
            + r"\b(?:(?:veio|ficou|esta|estava)\s+)?"
            + _NAO_ANTES
            + r"(?:faltando|faltou|falta)\s+"
            + _ANTES_DA_NOTA
        ),
        "nao veio ",
    ),
)
# O documento SOZINHO no turno ("a notafiscal?", "Nota fiscal por favor", "boa tarde, nota
# fiscal", "a nf do pedido?") é o pedido da NF — é o que o cliente escreve no lugar do "2" do
# menu. Só com a nota nomeada por inteiro: a "nota" sozinha pode ser a da avaliação ("a nota?")
# e "tem/vem com/sem nota fiscal?" (a pergunta da compra) não é o documento sozinho.
_SO_O_DOCUMENTO = re.compile(
    r"(?:e\s+)?(?:(?:a|minha|a\s+minha)\s+)?(?:nota\s+fiscal|nf|nfe)"
    r"(?:\s+(?:do|da|deste|desse|desta|dessa)\s+(?:meu\s+|minha\s+)?(?:pedido|compra|produto))?"
)


def _grafia(plano: str) -> str:
    """A grafia do documento, o aparelho e a ausência na forma que as réguas leem. PURA."""
    plano = " ".join(plano.split())
    for regra, troca in (*_GRAFIAS, (_O_APARELHO, "produto"), *_SEM_A_NOTA):
        plano = regra.sub(troca, plano)
    return plano


def _so_o_documento(plano: str) -> bool:
    """O turno é só o documento (e a cortesia)? PURA."""
    palavras = [p for p in re.findall(r"[a-z0-9]+", plano) if p not in _CORTESIA]
    return bool(_SO_O_DOCUMENTO.fullmatch(" ".join(palavras)))


# O aviso da Shopee no lugar da fala que o cliente apagou (30/10: segurava 6 turnos em 60 dias,
# "bom dia || This message was deleted || o pedido chegou || mas não veio a nota fiscal").
_APAGADA = "this message was deleted"


def pedido_claro(textos: Sequence[str | None]) -> bool:
    """O turno do cliente PEDE a NF do pedido, e só isso? PURA."""
    falas = [_grafia(validador.plano_de(t)) for t in textos if t]
    falas = [f for f in falas if " ".join(f.split()).strip(" .") != _APAGADA]
    plano = " ".join(" ".join(falas).split())
    if not plano or len(plano) > 400:
        return False
    if not (_PEDE.search(plano) or _COMO_RECEBO.search(plano) or _so_o_documento(plano)):
        return False
    if _NAO_E_SO_A_NF.search(plano) or not _NOMEIA_A_NF.search(plano):
        return False
    trechos = _TRECHOS.split(" . ".join(falas))
    if any(_NAO_VEIO.search(t) and not _NOME_DO_DOC.search(t) for t in trechos):
        return False
    resto = _O_CLIENTE_FAZ.sub(" ", _COMO_RECEBO.sub(" ", plano))
    resto = _MAS_NAO.sub(" ", _GRUDADO_NO_PAPEL.sub(" ", resto))
    return all(p in _PALAVRAS_DO_PEDIDO for p in re.findall(r"[a-z0-9]+", resto))


# ── O rascunho do agente só com a NF (PURA) ───────────────────────────────


def _agente(rascunho: Any) -> dict:
    fatos = getattr(rascunho, "fatos", None)
    agente = fatos.get("agente") if isinstance(fatos, dict) else None
    return agente if isinstance(agente, dict) else {}


def candidato(rascunho: Any) -> bool:
    """O rascunho do agente pede pessoa SÓ pela ação "Enviar o arquivo da NF"? PURA.

    É o que leva o `ia._talvez_enviar` a este caminho (os outros seguem o de
    sempre); a decisão de verdade é a `motivo_do_rascunho` + as travas.
    """
    from app.services.atendimento import ia

    return ia.rascunho_do_agente(rascunho) and ia.acoes_pessoa_do_rascunho(rascunho) == [
        constantes.ACAO_ENVIAR_ARQUIVO_NF
    ]


def motivo_do_rascunho(rascunho: Any) -> str | None:
    """Por que o rascunho não serve para a NF sozinha (None = serve). PURA.

    Só a NF no turno: o assunto `nota_fiscal`, gerado no automático, sem
    aviso, sem pista de outro assunto, sem checagem pendente, e o `sem_auto`
    só com a ação da NF.
    """
    if not candidato(rascunho):
        return "outra_acao"
    if getattr(rascunho, "categoria", None) != CATEGORIA:
        return "assunto"
    agente = _agente(rascunho)
    if agente.get("modo") not in (None, constantes.MODO_AUTO):
        return "gerado_no_copiloto"
    for chave in ("avisos_auto", "pistas_alem", "pistas_orientacao", "checagens"):
        if agente.get(chave):
            return chave
    if any(p != CATEGORIA for p in agente.get("pistas_turno") or []):
        return "outro_assunto"
    if list(agente.get("sem_auto") or []) != [MOTIVO_SEM_AUTO_NF]:
        return "sem_auto"
    return None


# ── A nota certa, do comprador certo ──────────────────────────────────────


def _cpf_da_nf(xml: bytes) -> str:
    nota = danfe.ler_nota(xml)
    return "".join(c for c in nota.dest["doc"] if c.isdigit())


def conferir_xml(xml: bytes, nota: Any) -> str | None:
    """A chave da linha = a do XML, e a chave bate com o número e o emitente. PURA.

    A chave de acesso: cUF(2) AAMM(4) CNPJ(14) mod(2) série(3) nNF(9) …
    """
    try:
        lida = danfe.ler_nota(xml)
    except danfe.DanfeInvalida:
        return "xml_invalido"
    chave = str(nota.chave or "")
    if lida.chave != chave or len(chave) != 44:
        return "chave_do_xml"
    if int(chave[25:34]) != int("".join(c for c in str(nota.numero) if c.isdigit()) or -1):
        return "chave_numero"
    cnpj = "".join(c for c in str(nota.emitente_cnpj or "") if c.isdigit())
    if cnpj and chave[6:20] != cnpj.rjust(14, "0"):
        return "chave_emitente"
    if lida.tp_amb != "1" or not lida.protocolo:
        return "nao_autorizada"
    return None


async def pedidos_recentes_do_comprador(
    session: AsyncSession, conversa: AtendimentoConversa, agora: datetime
) -> int:
    """Quantos pedidos este comprador tem nesta loja na janela (o índice da Shopee)."""
    return int(
        await session.scalar(
            select(func.count(func.distinct(AtendimentoPedidoComprador.pedido))).where(
                AtendimentoPedidoComprador.integration_id == conversa.integration_id,
                AtendimentoPedidoComprador.comprador_id == str(conversa.comprador_id or ""),
                or_(
                    AtendimentoPedidoComprador.criado_em.is_(None),
                    AtendimentoPedidoComprador.criado_em >= agora - JANELA_PEDIDOS,
                ),
            )
        )
        or 0
    )


async def conferir_nf(
    session: AsyncSession, conversa: AtendimentoConversa, agora: datetime | None = None
) -> tuple[danfe.NfDoPedido, bytes]:
    """A nota cheia do pedido DESTA conversa e DESTE comprador, com o XML. Levanta `NaoSai`."""
    agora = agora or datetime.now(UTC)
    pedido_mkt = str(conversa.pedido_marketplace or "").strip()
    comprador = str(conversa.comprador_id or "").strip()
    if not pedido_mkt or not comprador or conversa.integration_id is None:
        raise NaoSai("sem_pedido_ou_comprador")
    if conversa.plataforma == "shopee":
        # O índice da Shopee (`buyer_user_id` = o `to_id` do chat): o pedido de
        # outro comprador, ou mais de um pedido dele na loja, seguram já aqui.
        dono = set(
            (
                await session.execute(
                    select(AtendimentoPedidoComprador.comprador_id).where(
                        AtendimentoPedidoComprador.integration_id == conversa.integration_id,
                        AtendimentoPedidoComprador.pedido == pedido_mkt,
                    )
                )
            )
            .scalars()
            .all()
        )
        if not dono:
            raise NaoSai("comprador_nao_conferido")
        if dono != {comprador}:
            raise NaoSai("pedido_de_outro_comprador")
        if await pedidos_recentes_do_comprador(session, conversa, agora) > 1:
            raise NaoSai("varios_pedidos_do_comprador")
    if await pedidos_nos_cartoes(session, conversa, agora) - _ids_do_pedido(conversa):
        raise NaoSai("varios_pedidos_do_comprador")
    try:
        nf = await danfe.nf_da_conversa(session, conversa)
    except danfe.NfIndisponivel as e:
        raise NaoSai(e.code) from e
    if motivo := await _nota_unica(session, nf):
        raise NaoSai(motivo)
    # O pedido do Bling achado É o da conversa (nada de chave reserva).
    linhas = (
        await session.execute(
            select(BlingOrder.numeroloja, BlingOrder.documento_destinatario).where(
                BlingOrder.numero == nf.pedido_bling
            )
        )
    ).all()
    if not linhas or not {str(n or "").strip() for n, _ in linhas} <= _ids_do_pedido(conversa):
        raise NaoSai("pedido_do_bling_diferente")
    docs = {"".join(c for c in str(d or "") if c.isdigit()) for _, d in linhas}
    if len(docs) != 1 or not next(iter(docs)):
        raise NaoSai("pedido_sem_documento")
    nota = await session.get(NfNota, nf.id)
    if nota is None or nota.pedido_bling != nf.pedido_bling:
        raise NaoSai("nf_sumiu")
    xml = bytes(nota.xml or b"")
    if motivo := conferir_xml(xml, nota):
        raise NaoSai(motivo)
    if _cpf_da_nf(xml) != next(iter(docs)):
        raise NaoSai("destinatario_diferente")
    # Por último (é ida à plataforma): o pedido é deste comprador e não foi
    # cancelado, ao vivo.
    dono, status = await pedido_na_plataforma(session, conversa)
    if dono != comprador:
        raise NaoSai("comprador_diferente_na_plataforma")
    if str(status or "").upper() in STATUS_CANCELADOS.get(conversa.plataforma, frozenset()):
        raise NaoSai("pedido_cancelado_na_plataforma")
    return nf, xml


# O pedido cancelado (ou voltando) na plataforma, que o Bling ainda não viu.
STATUS_CANCELADOS: dict[str, frozenset[str]] = {
    "shopee": frozenset({"CANCELLED", "IN_CANCEL", "TO_RETURN"}),
    "tiktok": frozenset({"CANCELLED"}),
}
# A nota cheia não passa disto do total do pedido (frete/arredondamento).
TETO_DO_TOTAL = Decimal("1.05")


async def _nota_unica(session: AsyncSession, nf: danfe.NfDoPedido) -> str | None:
    """Revisão de 28/10 (A1): a NF escolhida é a ÚNICA cheia do pedido, não passa do
    total e não é de antes do pedido — a NF de outro pedido do mesmo CPF (ligada
    antes de o pedido dele chegar ao espelho) ou a reemitida (o `nf_nota` não sabe
    do cancelamento) ficam com a pessoa."""
    pedido = (
        await session.execute(
            select(func.max(BlingOrder.total), func.min(BlingOrder.data)).where(
                BlingOrder.numero == nf.pedido_bling
            )
        )
    ).one()
    total, data_pedido = pedido
    notas = (
        await session.execute(
            select(NfNota.valor, NfNota.data_emissao, NfNota.xml).where(
                NfNota.pedido_bling == nf.pedido_bling,
                NfNota.situacao == danfe.SITUACAO_AUTORIZADA,
            )
        )
    ).all()
    cheias = [n for n in notas if danfe._eh_cheia(n.valor, total) and danfe._saida_normal(n.xml)]
    if len(cheias) != 1:
        return "mais_de_uma_nf_cheia"
    valor = Decimal(str(cheias[0].valor or 0))
    if total is None or valor > Decimal(str(total)) * TETO_DO_TOTAL:
        return "nf_acima_do_pedido"
    emitida = cheias[0].data_emissao
    if data_pedido is None or emitida is None or _dia(emitida) < _dia(data_pedido):
        return "nf_antes_do_pedido"
    return None


def _dia(quando: datetime):
    from zoneinfo import ZoneInfo

    return quando.astimezone(ZoneInfo("America/Sao_Paulo")).date()


def _ids_do_pedido(conversa: AtendimentoConversa) -> set[str]:
    """Os números com que o pedido DESTA conversa pode estar no `numeroloja` do Bling.

    Shopee/TikTok: o pedido da conversa. ML pós-venda: a conversa É o pacote
    (`externo_id`); o Bling guarda o pack ou o order (`dados.pack_id`/`order_id`).
    """
    ids = {str(conversa.pedido_marketplace or "").strip()}
    if conversa.plataforma == "ml" and conversa.canal == constantes.CANAL_POS_VENDA:
        dados = conversa.dados if isinstance(conversa.dados, dict) else {}
        ids |= {
            str(v).strip()
            for v in (conversa.externo_id, dados.get("pack_id"), dados.get("order_id"))
            if v
        }
    return {i for i in ids if i}


async def pedidos_nos_cartoes(
    session: AsyncSession, conversa: AtendimentoConversa, agora: datetime
) -> set[str]:
    """Os pedidos dos cartões de pedido da conversa na janela (de quais o comprador falou)."""
    linhas = (
        (
            await session.execute(
                select(AtendimentoMensagem.anexos).where(
                    AtendimentoMensagem.conversa_id == conversa.id,
                    AtendimentoMensagem.tipo == "pedido",
                    AtendimentoMensagem.created_at >= agora - JANELA_PEDIDOS,
                )
            )
        )
        .scalars()
        .all()
    )
    pedidos: set[str] = set()
    for anexos in linhas:
        for a in anexos or []:
            if isinstance(a, dict) and a.get("tipo") == "pedido" and a.get("pedido"):
                pedidos.add(str(a["pedido"]).strip())
    return pedidos


async def pedido_na_plataforma(
    session: AsyncSession, conversa: AtendimentoConversa
) -> tuple[str | None, str | None]:
    """(comprador, status) do pedido da conversa NA PLATAFORMA, agora (None = não deu).

    Shopee: `get_order_detail` com `buyer_user_id` (o `to_id` do chat) e o
    `order_status`. TikTok: o `user_id` do pedido (o mesmo do participante
    BUYER do chat) e o `status`. ML pós-venda: a conversa é a do pacote do
    pedido — o comprador é o dela (o ML bloqueia a mensagem do cancelado).
    """
    from app.models import Integration
    from app.services.atendimento import clientes

    if conversa.plataforma == "ml" and conversa.canal == constantes.CANAL_POS_VENDA:
        return str(conversa.comprador_id or "").strip() or None, None
    integration = await session.get(Integration, conversa.integration_id)
    if integration is None:
        return None, None
    pedido = str(conversa.pedido_marketplace or "").strip()
    try:
        cliente = await clientes.cliente_da_integracao(integration)
        if conversa.plataforma == "shopee":
            o = await cliente.get_order_buyer(pedido) or {}
            return str(o.get("buyer_user_id") or "").strip() or None, o.get("order_status")
        if conversa.plataforma == "tiktok":
            o = await cliente.get_order_detail(pedido) or {}
            return str(o.get("user_id") or "").strip() or None, o.get("status")
    except Exception as e:  # noqa: BLE001 — sem saber, não sai
        logger.info(
            "atendimento_nf_auto_comprador_indisponivel",
            conversa_id=str(conversa.id),
            erro=type(e).__name__,
        )
    return None, None


async def ja_enviada(
    session: AsyncSession,
    conversa: AtendimentoConversa,
    *,
    chave: str,
    sha256: str,
    numero: str | None = None,
) -> bool:
    """Esta NF já saiu ou está saindo nesta conversa? Pela chave, pela mesma imagem/PDF
    ou (revisão de 28/10, B1: o desenho pode mudar num deploy) pelo NOME do anexo que
    o botão "NF" dá (`NF_<número>.pdf`, `.jpg` na conversa que não aceita PDF)."""
    achada = await session.scalar(
        select(AtendimentoMensagem.id)
        .where(
            AtendimentoMensagem.conversa_id == conversa.id,
            AtendimentoMensagem.status != constantes.MSG_FALHOU,
            or_(
                AtendimentoMensagem.payload["nf"]["chave"].astext == chave,
                AtendimentoMensagem.payload["foto"]["sha256"].astext == sha256,
                *(
                    AtendimentoMensagem.anexos.contains([{"nome": f"NF_{numero}.{ext}"}])
                    for ext in ("jpg", "pdf")
                    if numero
                ),
            ),
        )
        .limit(1)
    )
    return achada is not None


# ── O caminho (chamado pelo `ia._talvez_enviar`) ──────────────────────────


async def _travas_da_conversa(
    session: AsyncSession, conversa: AtendimentoConversa, rascunho: AtendimentoRascunho
) -> str | None:
    """As travas do envio sozinho que não dependem da NF (as do `ia._talvez_enviar`)."""
    from app.services.atendimento import agente_config, ia

    s = get_settings()
    if not s.atendimento_auto_ativo:
        return "auto_desligado"
    if not s.atendimento_nf_automatica:
        return "nf_automatica_desligada"
    if rascunho.status != constantes.RASCUNHO_PENDENTE:
        return "rascunho_nao_pendente"
    if (rascunho.fatos or {}).get(ia.FATO_PEDIDA_PELA_TELA):
        return "pedida_pela_tela"
    if motivo := motivo_do_rascunho(rascunho):
        return motivo
    if not s.atendimento_agente_verificador:
        # Revisão de 28/10: com o verificador desligado, NENHUM rascunho do agente sai
        # sozinho — a NF também não.
        return "verificador_desligado"
    if (rascunho.confianca or 0.0) < ia.confianca_minima(True):
        return "confianca_baixa"
    await session.refresh(conversa)
    if (conversa.plataforma, conversa.canal) not in CAIXAS:
        return "caixa_sem_nf_automatica"
    if not conversa.aguardando_resposta:
        return "ja_respondida"
    if conversa.ia_pausada:
        return "ia_pausada"
    if conversa.atribuido_a is not None:
        return "atribuida"
    if conversa.sem_resposta_necessaria:
        return "sem_resposta_necessaria"
    if conversa.situacao in (constantes.CONVERSA_FECHADA, constantes.CONVERSA_BLOQUEADA):
        return "conversa_fechada_ou_bloqueada"
    if constantes.reclamacao_aberta(conversa.dados):
        return "reclamacao_aberta"
    if conversa.plataforma in constantes.PLATAFORMAS_SEM_AUTO:
        return "plataforma_sem_auto"
    if constantes.motivo_canal_sem_envio(conversa.canal, conversa.plataforma, conversa.dados):
        return "canal_sem_envio"
    canal = (
        await session.get(AtendimentoCanal, conversa.canal_id, populate_existing=True)
        if conversa.canal_id is not None
        else None
    )
    if canal is None or canal.modo != constantes.MODO_AUTO:
        return "canal_fora_do_auto"
    if not agente_config.agente_ligado(s, canal):
        return "agente_desligado"
    # A régua dos assuntos do automático: o `nota_fiscal` marcado na loja.
    if CATEGORIA not in (canal.auto_categorias or []):
        return "assunto_nao_liberado"
    if CATEGORIA not in (canal.auto_agente or []):
        return "assunto_nao_liberado_agente"
    from app.services.atendimento import manual as manual_svc

    if CATEGORIA in manual_svc.ids_so_humano(await manual_svc.categorias_ativas(session)):
        return "assunto_so_humano"
    if ia.andamento_no_rascunho(rascunho, ia._etiquetas_da_conversa(conversa)):
        return "em_andamento"
    # O pedido claro, pelo turno do cliente que o rascunho responde.
    recentes = await ia._mensagens_recentes(session, conversa)
    gatilho = (
        await session.get(AtendimentoMensagem, rascunho.mensagem_gatilho_id)
        if rascunho.mensagem_gatilho_id is not None
        else None
    )
    if gatilho is None:
        return "sem_gatilho"
    ultima = await ia._ultima_do_cliente(session, conversa)
    if ultima is None or not ia._cobre(rascunho, ultima):
        # O cliente escreveu de novo depois do turno que este rascunho responde.
        return "turno_mudou"
    textos = textos_do_turno(recentes, ia.rajada_do_cliente(recentes, gatilho))
    if not pedido_claro(textos) and not pedido_pelo_menu(recentes, gatilho):
        return "pedido_nao_claro"
    if await ia._motivos_humano_para_auto(session, conversa, rascunho, nf_automatica=True):
        return "caixa_humano"
    if await ia._pausa_humana_ate(session, conversa) is not None:
        return "pausa_humana"
    return None


async def talvez_enviar(
    session: AsyncSession, conversa: AtendimentoConversa, rascunho: AtendimentoRascunho
) -> bool:
    """Confere tudo e manda a NF + a frase. True = tentou o envio. Nunca levanta.

    Não sai → registra `nf:<motivo>` (o rascunho fica para a pessoa, com o
    "Anexar a NF" no cartão da sugestão).
    """
    from app.services.atendimento import enviar, foto, ia

    tentou = False
    try:
        try:
            motivo = await _travas_da_conversa(session, conversa, rascunho)
            if motivo is not None:
                raise NaoSai(motivo)
            nf, xml = await conferir_nf(session, conversa)
            pdf = await asyncio.to_thread(danfe.gerar_pdf, xml)
            anexo = await asyncio.to_thread(
                foto.validar_para_a_caixa, conversa, pdf, nf.nome_arquivo, foto.PDF
            )
            chave = danfe.ler_nota(xml).chave
            if await ja_enviada(
                session, conversa, chave=chave, sha256=anexo.sha256, numero=nf.numero
            ):
                raise NaoSai("nf_ja_enviada")
        except NaoSai as e:
            await ia._registrar_auto_envio(session, conversa, rascunho, f"nf:{e.code}")
            logger.info(
                "atendimento_nf_auto_segurada",
                conversa_id=str(conversa.id),
                rascunho_id=str(rascunho.id),
                motivo=e.code,
            )
            return False
        tentou = True
        try:
            imagem, texto = await enviar.enviar_nf_automatica(
                session,
                conversa,
                anexo,
                rascunho=rascunho,
                chave=chave,
                numero=nf.numero,
                legenda=LEGENDA,
            )
        except enviar.EnvioRecusado as e:
            await ia._registrar_auto_envio(session, conversa, rascunho, f"nf:recusado:{e.code}")
            logger.info(
                "atendimento_nf_auto_recusada",
                conversa_id=str(conversa.id),
                rascunho_id=str(rascunho.id),
                code=e.code,
            )
            return True
        status = "enviado_nf" if imagem.status == constantes.MSG_ENVIADA else f"nf:{imagem.status}"
        await ia._registrar_auto_envio(session, conversa, rascunho, status)
        logger.info(
            "atendimento_nf_auto_enviada",
            conversa_id=str(conversa.id),
            rascunho_id=str(rascunho.id),
            nf_id=nf.id,
            imagem=imagem.status,
            texto=texto.status if texto is not None else None,
        )
        return True
    except Exception as e:  # noqa: BLE001 — a sugestão está salva; fica com a pessoa
        logger.warning(
            "atendimento_nf_auto_falhou",
            conversa_id=str(conversa.id),
            rascunho_id=str(rascunho.id),
            err=type(e).__name__,
        )
        await ia._desfazer(session, conversa, rascunho)
        try:
            await ia._registrar_auto_envio(session, conversa, rascunho, "nf:falha_interna")
        except Exception:  # noqa: BLE001
            await ia._desfazer(session, conversa, rascunho)
        return tentou
