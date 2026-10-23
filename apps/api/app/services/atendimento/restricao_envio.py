"""RESTRIÇÃO DE ENVIO POR REGIÃO para o AGENTE (20/10/2026).

O caso: "entrega no Rio?". Três fontes dizem que um anúncio não vai para um
estado — o agente vê de qual veio (`fonte`) e responde pela regra
`cobertura_regiao` do Manual (aprovada em 20/10/2026: citar o anúncio e
sugerir conferir pelo CEP); depois da compra, pedido para estado restrito é
de pessoa.

  1. "anuncio": a DESCRIÇÃO CRUA do anúncio (a ficha LIMPA tira as frases de
     envio — `ficha._DESCRICAO_FORA` —, então lê-se o que a loja devolveu: a
     ficha fresca do Redis `atd:ficha:*`, até 8.000 caracteres, ou a linha
     do catálogo `listings` com o mesmo corte de data e de título da ficha)
     e o título. Vale a NEGAÇÃO seguida do estado ("Não enviamos para o Rio
     de Janeiro", "NÃO ENTREGAMOS NO RJ", "Proibido envio para RJ", "Não
     atendemos o estado do Rio de Janeiro", "…todo o Brasil, exceto RJ");
     a frase positiva ("enviamos para todo o Brasil, inclusive RJ") e a
     negação que não restringe ("não enviamos SÓ para SP") não valem. O
     `trecho` é a frase do anúncio (sem contato fora da loja), para citar;
  2. "regra_apple_shopee": a regra fixa do faturamento automático (Shopee +
     Apple → RJ não), pelas MESMAS constantes de `nf_auto_enfileirar` (por
     import: a regra não é copiada aqui);
  3. "loja": `store_info.uf_restrictions` + `excecoes` (tela Lojas), avaliados
     pela MESMA função do faturamento (`nf_auto_enfileirar.avaliar_restricao_loja`):
     loja sem exceção válida fica inerte (como no faturamento); exceção de
     "palavra"/"sku" que casa com os produtos do anúncio libera; a de
     "valor" depende do pedido — todo preço conhecido acima do limite: o
     estado vai em `ufs`; senão (sem preço, ou abaixo do limite: duas
     unidades, ou a variação mais cara, passam dele e o faturamento olha o
     TOTAL do pedido) vai em `ufs_conforme_pedido` (o valor nunca vai ao
     modelo; revisão de 20/10/2026 — antes, preço abaixo do limite tirava o
     estado e o agente respondia "entrega" para o pedido que o faturamento
     bloqueia).
A ligação loja ↔ integração: `store_info.integration_id`, ou o
`bling_store_id` da loja (`stores.integration_id` / `integrations.bling_loja_id`)
— em produção (20/10/2026) as 16 lojas Shopee com "RJ" não têm o
`integration_id` preenchido; 10 casam pelo `stores`.

PÓS-VENDA (`pedido_para_uf_restrita`): só True/False — a UF do comprador
nunca volta ao modelo. As mesmas regras do faturamento sobre as linhas do
pedido no Bling (Apple→RJ na Shopee; a restrição da loja com o valor do
pedido) e, se o chamador passar, as UFs do anúncio.

SÓ LEITURA: nada aqui escreve (o bloqueio do pedido é do faturamento,
`nf_auto_enfileirar`).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BlingOrder, Integration, Product, ProductLink, Store, StoreInfo
from app.models.enums import IntegrationPlatform
from app.services.atendimento import ficha as ficha_svc
from app.services.atendimento.validador import contato_fora_da_loja, plano_de
from app.services.nf_auto_enfileirar import (
    _RESTRICAO_KEYWORDS,
    _RESTRICAO_PLATAFORMAS,
    _RESTRICAO_UF,
    avaliar_restricao_loja,
)

logger = structlog.get_logger()

FONTE_ANUNCIO = "anuncio"
FONTE_LOJA = "loja"
FONTE_REGRA_APPLE = "regra_apple_shopee"
# Ordem de citação: o anúncio (o cliente vê), a regra fixa, a loja.
_ORDEM_FONTES = (FONTE_ANUNCIO, FONTE_REGRA_APPLE, FONTE_LOJA)

MAX_CHARS_TRECHO = 200
# A descrição crua lida (a mesma da ficha fresca).
MAX_CHARS_DESCRICAO = ficha_svc.MAX_CHARS_DESCRICAO_BRUTA
# Vínculos lidos para avaliar a exceção da loja.
MAX_VINCULOS = 60

UFS: dict[str, str] = {
    "AC": "acre",
    "AL": "alagoas",
    "AP": "amapa",
    "AM": "amazonas",
    "BA": "bahia",
    "CE": "ceara",
    "DF": "distrito federal",
    "ES": "espirito santo",
    "GO": "goias",
    "MA": "maranhao",
    "MT": "mato grosso",
    "MS": "mato grosso do sul",
    "MG": "minas gerais",
    "PA": "para",
    "PB": "paraiba",
    "PR": "parana",
    "PE": "pernambuco",
    "PI": "piaui",
    "RJ": "rio de janeiro",
    "RN": "rio grande do norte",
    "RS": "rio grande do sul",
    "RO": "rondonia",
    "RR": "roraima",
    "SC": "santa catarina",
    "SP": "sao paulo",
    "SE": "sergipe",
    "TO": "tocantins",
}
# A sigla em minúscula só quando não é palavra comum ("se", "to", "ma", "pa",
# "es", "am", "al", "ap", "pr" ficam só em MAIÚSCULA).
_SIGLAS_MINUSCULAS = frozenset("rj sp mg rs sc ba ce pe pb rn go mt ms df ro rr ac pi".split())

# ── A frase do anúncio (pura) ─────────────────────────────────────────────

# Marcas que sobrevivem ao `plano_de` (minúsculas, sem acento): a sigla
# escrita como sigla e o Pará com acento ("para" sem acento é preposição).
_MARCA = "ufx"
_SIGLA = re.compile(r"\b(" + "|".join(UFS) + r")\b")
_SIGLA_MINUSCULA = re.compile(r"\b(" + "|".join(sorted(_SIGLAS_MINUSCULAS)) + r")\b")
_PARA_COM_ACENTO = re.compile(r"\bpar[áÁ]\b", re.IGNORECASE)
# O Pará SEM acento (revisão de 20/10/2026, Barbosa: "NÃO ENVIAMOS PARA OS
# ESTADOS DA BAHIA E DO PARA!"): só onde "para" não pode ser a preposição —
# depois de "estado(s) do/da" e de "e do" (o item de uma lista).
_PARA_SEM_ACENTO = re.compile(
    r"\b((?:estados?\s+d[oa]s?|e\s+do|ou\s+do|nem\s+do)\s+)para\b"
    r"(?!\s+(?:o|a|os|as|um|uma|todo|toda|todos|todas|voce|vc|nos|quem|que)\b)"
)
# "p/ RJ": a abreviação da preposição.
_P_BARRA = re.compile(r"\bp\s*/\s*", re.IGNORECASE)

# O nome do estado (no texto plano); os mais longos primeiro ("mato grosso do
# sul" antes de "mato grosso").
_NOMES = sorted(((nome, uf) for uf, nome in UFS.items() if uf != "PA"), key=lambda x: -len(x[0]))
# "Rio" sozinho é o RJ (revisão 2 de 20/10/2026: "Não entregamos no Rio",
# "Sim, entregamos no Rio") — nunca "Rio Grande do Sul", "Rio Branco", "Rio
# Verde", "rio de contas": depois do nome completo e sem outra palavra de lugar.
_RIO = "rio"
_RIO_SOZINHO = (
    r"rio(?!\s+(?:de|do|da|dos|das|grande|branco|verde|claro|preto|bonito|novo|pardo|negro|largo"
    r"|real|acima|abaixo|doce|vermelho|azul|sao|santo|santa)\b)"
)
_ALVO = (
    r"\b(?:(?:o|a|d[oa]s?)\s+)?(?:(?:estado|estados|cidade|regiao|capital)\s+d[oae]s?\s+)?"
    r"(?P<alvo>"
    + _MARCA
    + r"[a-z]{2}|"
    + "|".join(re.escape(n) for n, _ in _NOMES)
    + "|"
    + _RIO_SOZINHO
    + r")\b"
)
_ALVO_RE = re.compile(_ALVO)
# Mais estados em lista ("RJ, ES e MG"): cada um seguido de outro separador
# ou do fim — "RJ, SE TIVER DÚVIDA" não põe Sergipe na lista.
_DEPOIS = re.compile(
    r"\s*(?:,|/|;|\be\b|\bou\b|\bnem\b)\s*(?:(?:e|ou|nem)\s+)?"
    + _ALVO
    + r"(?=\s*(?:[,/;.!)]|$|\be\b|\bou\b|\bnem\b))"
)
# A negação que restringe: o verbo de enviar/entregar/atender negado, a
# proibição, a exceção ("…exceto RJ").
_NEGACAO = re.compile(
    r"\b(?:nao|nem)\s+(?:(?:fazemos|realizamos|efetuamos|temos|ha|oferecemos"
    # Ajuste rf3 (21/10/2026): a 3ª pessoa ("este anúncio não faz envios para
    # o Rio de Janeiro"), a passiva ("não pode ser enviado", "o envio não é
    # feito"), "não chega ao", "não temos como enviar", "não está disponível
    # para" — a recusa CERTA do RJ dita assim era lida como entrega.
    r"|faz|fazem|realiza|realizam|efetua|efetuam|oferece|oferecem)\s+(?:o\s+|a\s+)?"
    r"(?:envios?|entregas?|fretes?)"
    r"|(?:conseguimos|podemos|vamos)\s+(?:enviar|entregar|atender|despachar|mandar|vender)"
    r"|(?:e|sera|sao|serao)\s+(?:possivel\s+(?:enviar|entregar)|enviad[oa]s?|entregue?s?"
    r"|feit[oa]s?|realizad[oa]s?|efetuad[oa]s?|atendid[oa]s?|vendid[oa]s?)"
    r"|(?:pode|podem|podera|poderao)\s+ser\s+(?:enviad|entregu|despachad|mandad|vendid|atendid)\w*"
    r"|(?:temos|tem|ha|existe)\s+como\s+(?:enviar|entregar|mandar|despachar|atender|vender)"
    r"|(?:chega|chegam|chegara|chegarao|vai\s+chegar)"
    r"|(?:esta|estao|fica|ficam)\s+disponive(?:l|is)"
    r"|(?:enviamos|entregamos|atendemos|despachamos|mandamos|vendemos|envia|entrega|atende"
    r"|enviam|entregam|atendem|enviad[oa]s?|entregues?))\b"
    r"|\bproibid[oa]s?\s+(?:o\s+|a\s+)?(?:envios?|entregas?|vendas?)\b"
    r"|\b(?:envios?|entregas?|vendas?)\s+(?:proibid|vetad|bloquead|suspens|indisponive)\w*"
    r"|\bsem\s+(?:envios?|entregas?)\b"
    # Revisão "furos": "o anúncio tem restrição de envio para o Rio de Janeiro".
    r"|(?<!sem\s)(?<!nao\sha\s)(?<!nao\stem\s)(?<!nao\stemos\s)(?<!nao\sexiste\s)"
    r"(?<!nenhuma\s)(?<!qualquer\s)"
    r"\brestric\w+\s+(?:de\s+(?:envio|entrega|frete)s?\s+)?"
)
# A EXCEÇÃO ("…todo o Brasil, exceto RJ") só restringe numa frase de
# COBERTURA (para onde se envia) e fora de frase de prazo ou frete ("Entrega
# em 2 dias úteis exceto RJ", "Frete grátis para todo o Brasil, exceto RJ":
# o RJ recebe, com outro prazo ou frete). "Pelo menos" não é exceção.
_EXCECAO = re.compile(r"\b(?:exceto|excecao\s+d[oae]s?|salvo|(?<!pelo\s)menos)\b")
_COBERTURA = re.compile(
    r"\btod[oa]\s+o\s+(?:brasil|pais|territorio)\b|\btodos\s+os\s+estados\b"
    r"|\btodas\s+as\s+(?:regioes|cidades)\b"
    r"|\b(?:envi|entreg|atend|despach|mand)\w*\s+(?:\w+\s+){0,3}?(?:para|pra|a)\s+tod[oa]s?\b"
)
_PRAZO_OU_FRETE = re.compile(
    r"\b(?:prazos?|dias?|horas?|uteis|frete|fretes|gratis|de\s+graca|chega\w*|leva\w*)\b"
)
# O que pode haver entre a negação e o estado ("não enviamos ESTE PRODUTO
# PARA O RJ"); "só", "apenas", "todo", "inclusive" desfazem a restrição.
_ENTRE_OK = frozenset(
    """
    para pra pro o a os as no na nos nas ao aos em de do da dos das estado estados
    cidade cidades regiao regioes envio envios entrega entregas este esse deste desse
    produto produtos item itens pedido pedidos anuncio momento nenhum nenhuma mais
    ainda infelizmente nosso nossos nossa nossas frete fretes compras compra
    """.split()
)
_ENTRE_MAX = 8
_SENTENCAS = re.compile(r"(?<=[.!?;])\s+|\n+")
_PALAVRA = re.compile(r"[a-z0-9_]+")


def _marcar(sentenca: str) -> str:
    """A sentença no texto plano, com as siglas e o Pará marcados (`ufx` + sigla)."""
    t = _P_BARRA.sub("para ", sentenca)
    t = _PARA_COM_ACENTO.sub(_MARCA + "pa", t)
    t = _SIGLA.sub(lambda m: _MARCA + m.group(1).lower(), t)
    t = plano_de(t)
    t = _PARA_SEM_ACENTO.sub(lambda m: m.group(1) + _MARCA + "pa", t)
    return _SIGLA_MINUSCULA.sub(lambda m: _MARCA + m.group(1), t)


def _uf_do_alvo(alvo: str) -> str | None:
    if alvo.startswith(_MARCA):
        uf = alvo[len(_MARCA) :].upper()
        return uf if uf in UFS else None
    for nome, uf in _NOMES:
        if alvo == nome:
            return uf
    return "RJ" if alvo == _RIO else None


def ufs_negadas(sentenca: str) -> list[str]:
    """As UFs que a sentença diz que NÃO são atendidas, na ordem — [] = nenhuma. PURA."""
    t = _marcar(sentenca)
    saida: list[str] = []
    excecoes = [
        m
        for m in _EXCECAO.finditer(t)
        if _COBERTURA.search(t[: m.start()]) and not _PRAZO_OU_FRETE.search(t[: m.start()])
    ]
    for neg in sorted([*_NEGACAO.finditer(t), *excecoes], key=lambda m: m.start()):
        alvo = _ALVO_RE.search(t, neg.end())
        if alvo is None:
            continue
        entre = _PALAVRA.findall(t[neg.end() : alvo.start()])
        if len(entre) > _ENTRE_MAX or any(p not in _ENTRE_OK for p in entre):
            continue
        uf = _uf_do_alvo(alvo.group("alvo"))
        if uf is None:
            continue
        ufs = [uf]
        fim = alvo.end()
        while m := _DEPOIS.match(t, fim):
            if outra := _uf_do_alvo(m.group("alvo")):
                ufs.append(outra)
            fim = m.end()
        for u in ufs:
            if u not in saida:
                saida.append(u)
    # A negação DEPOIS do estado (revisão 2 de 20/10/2026): "pedidos para o RJ
    # não são enviados", "para o Rio, infelizmente, não enviamos".
    for alvo in _ALVO_RE.finditer(t):
        fim = alvo.end()
        ufs = [_uf_do_alvo(alvo.group("alvo"))]
        while m := _DEPOIS.match(t, fim):
            ufs.append(_uf_do_alvo(m.group("alvo")))
            fim = m.end()
        if not (neg := _NEGACAO_DEPOIS.match(t, fim)):
            continue
        if any(p not in _ENTRE_OK for p in _PALAVRA.findall(neg.group("entre") or "")):
            continue
        for u in ufs:
            if u and u not in saida:
                saida.append(u)
    return saida


_NEGACAO_DEPOIS = re.compile(
    r"\s*[,:]?\s*(?P<entre>(?:[a-z0-9]+\s*,?\s+){0,3}?)(?:nao|nem)\s+(?:(?:sao|e|serao|sera|estao"
    r"|esta|podem\s+ser|pode\s+ser|conseguimos|podemos|vamos)\s+)?(?:(?:possivel\s+)?(?:enviad|entregu"
    r"|atendid|despachad|aceit|vendid|postad|enviar|entregar|atender|despachar|mandar|vender)\w*"
    r"|enviamos|entregamos|atendemos|despachamos|mandamos|vendemos|chega|chegam|entrega|envia"
    r"|atende"
    # Ajuste rf3: "para o Rio de Janeiro não há envio", "não estão
    # disponíveis", "não é feito".
    r"|(?:ha|tem|temos|existe|fazemos|faz|fazem|realizamos|realiza)\s+(?:o\s+|a\s+)?(?:envios?"
    r"|entregas?)|disponive(?:l|is)|feit[oa]s?|realizad[oa]s?)\b"
)


# O estado como DESTINO ("para o RJ", "no Rio de Janeiro", "em SP") numa
# frase de entrega — não a origem ("feito de São Paulo").
_DESTINO = re.compile(r"\b(?:para|pra|pro|no|na|nos|nas|em|ao|ate)\s+" + _ALVO.removeprefix(r"\b"))
_FALA_DE_ENTREGA = re.compile(r"\b(?:entreg|envi|atend|mand|despach|cheg|vend|frete|receb)\w*")


def ufs_de_destino(sentenca: str) -> list[str]:
    """As UFs citadas como DESTINO de entrega na frase (afirmada ou negada). PURA.

    Para a checagem do texto do agente (`checagens.conferir_regiao`): a
    frase precisa falar de entrega/envio; o estado depois de "para", "no",
    "em"... O que ela NEGA é `ufs_negadas`.
    """
    t = _marcar(sentenca)
    if not _FALA_DE_ENTREGA.search(t):
        return []
    saida: list[str] = []
    for m in _DESTINO.finditer(t):
        ufs = [_uf_do_alvo(m.group("alvo"))]
        fim = m.end()
        while d := _DEPOIS.match(t, fim):
            ufs.append(_uf_do_alvo(d.group("alvo")))
            fim = d.end()
        for uf in ufs:
            if uf and uf not in saida:
                saida.append(uf)
    return saida


# As cidades grandes e os gentílicos (revisão "furos" de 20/10/2026: "moro em
# Niterói", "sou de Duque de Caxias", "sou carioca", "capital carioca"). Só os
# que não são palavra comum nem nome de mais de um estado: ficam de fora
# "natal", "vitoria", "palmas", "serra", "colombo", "caxias" sozinho, "sao
# jose", "boa vista", "gama", "santa rita", "campo grande" (também bairro do
# Rio), "contagem", "americana" ("versão americana"), "dourados", "queimados",
# "franca", "barreiras".
CIDADES_E_GENTILICOS: dict[str, str] = {
    **dict.fromkeys(
        "niteroi;sao goncalo;duque de caxias;nova iguacu;belford roxo;sao joao de meriti;"
        "petropolis;volta redonda;campos dos goytacazes;mage;itaborai;macae;cabo frio;"
        "angra dos reis;angra;nova friburgo;barra mansa;teresopolis;mesquita;nilopolis;"
        "marica;resende;araruama;itaguai;rio das ostras;seropedica;japeri;baixada fluminense;"
        "regiao dos lagos;carioca;cariocas;fluminense;fluminenses;niteroiense".split(";"),
        "RJ",
    ),
    **dict.fromkeys(
        "campinas;guarulhos;santo andre;sao bernardo do campo;osasco;sorocaba;ribeirao preto;"
        "santos;sao jose dos campos;maua;mogi das cruzes;diadema;jundiai;piracicaba;carapicuiba;"
        "bauru;itaquaquecetuba;sao vicente;praia grande;guaruja;taubate;limeira;sumare;"
        "suzano;barueri;aracatuba;marilia;presidente prudente;sao carlos;araraquara;"
        "indaiatuba;cotia;paulista;paulistas;paulistano;paulistana;paulistanos".split(";"),
        "SP",
    ),
    **dict.fromkeys(
        "belo horizonte;uberlandia;juiz de fora;betim;montes claros;uberaba;"
        "ribeirao das neves;governador valadares;ipatinga;sete lagoas;divinopolis;"
        "pocos de caldas;patos de minas;teofilo otoni;mineiro;mineira;mineiros".split(";"),
        "MG",
    ),
    **dict.fromkeys(
        "vila velha;cariacica;cachoeiro de itapemirim;linhares;guarapari;colatina;capixaba;"
        "capixabas".split(";"),
        "ES",
    ),
    **dict.fromkeys(
        "porto alegre;caxias do sul;pelotas;canoas;santa maria;gravatai;viamao;novo hamburgo;"
        "sao leopoldo;passo fundo;gaucho;gaucha;gauchos".split(";"),
        "RS",
    ),
    **dict.fromkeys(
        "florianopolis;floripa;joinville;blumenau;chapeco;itajai;criciuma;balneario camboriu;"
        "jaragua do sul;catarinense;catarinenses".split(";"),
        "SC",
    ),
    **dict.fromkeys(
        "curitiba;londrina;maringa;ponta grossa;cascavel;sao jose dos pinhais;foz do iguacu;"
        "guarapuava;paranaense;paranaenses".split(";"),
        "PR",
    ),
    **dict.fromkeys(
        "salvador;feira de santana;vitoria da conquista;camacari;itabuna;lauro de freitas;"
        "ilheus;jequie;teixeira de freitas;porto seguro;baiano;baiana;baianos;"
        "soteropolitano".split(";"),
        "BA",
    ),
    **dict.fromkeys(
        "recife;jaboatao;olinda;caruaru;petrolina;pernambucano;pernambucana".split(";"), "PE"
    ),
    **dict.fromkeys(
        "fortaleza;caucaia;juazeiro do norte;maracanau;sobral;crato;cearense;cearenses".split(";"),
        "CE",
    ),
    **dict.fromkeys(
        "belem;ananindeua;santarem;maraba;castanhal;paraense;paraenses".split(";"), "PA"
    ),
    **dict.fromkeys("sao luis;imperatriz;timon;maranhense;maranhenses".split(";"), "MA"),
    **dict.fromkeys(
        "goiania;aparecida de goiania;anapolis;luziania;goiano;goiana;goianos".split(";"), "GO"
    ),
    **dict.fromkeys("manaus;parintins;itacoatiara;amazonense;amazonenses".split(";"), "AM"),
    **dict.fromkeys(
        "brasilia;taguatinga;ceilandia;samambaia;planaltina;aguas claras;brasiliense;"
        "brasilienses;candango".split(";"),
        "DF",
    ),
    **dict.fromkeys(
        "cuiaba;varzea grande;rondonopolis;sinop;mato-grossense;matogrossense".split(";"), "MT"
    ),
    **dict.fromkeys("tres lagoas;corumba;sul-mato-grossense".split(";"), "MS"),
    **dict.fromkeys("joao pessoa;campina grande;paraibano;paraibana".split(";"), "PB"),
    **dict.fromkeys("mossoro;parnamirim;potiguar;potiguares".split(";"), "RN"),
    **dict.fromkeys("maceio;arapiraca;alagoano;alagoana".split(";"), "AL"),
    **dict.fromkeys("teresina;parnaiba;piauiense;piauienses".split(";"), "PI"),
    **dict.fromkeys("aracaju;sergipano;sergipana".split(";"), "SE"),
    **dict.fromkeys("porto velho;ji-parana;rondoniense".split(";"), "RO"),
    **dict.fromkeys("araguaina;tocantinense".split(";"), "TO"),
    **dict.fromkeys("acriano;acreano".split(";"), "AC"),
    **dict.fromkeys("macapa;amapaense".split(";"), "AP"),
    **dict.fromkeys("roraimense".split(";"), "RR"),
}
_CIDADE_RE = re.compile(
    r"\b("
    + "|".join(
        re.escape(c).replace(r"\ ", r"\s+")
        for c in sorted(CIDADES_E_GENTILICOS, key=len, reverse=True)
    )
    + r")\b"
)


def ufs_citadas(texto: str | None) -> list[str]:
    """Todas as UFs citadas no texto (sigla, nome, "Rio", cidade grande ou
    gentílico), na ordem. PURA.

    Para o turno do CLIENTE ("moro no Rio, entregam aí?", "sou do RJ", "moro
    em Niterói", "sou carioca"): a checagem do agente
    (`checagens.conferir_regiao`) sabe que ele perguntou de um estado
    (revisão 2 de 20/10/2026; as cidades e os gentílicos na revisão "furos").
    """
    saida: list[str] = []
    for sentenca in _SENTENCAS.split(texto or ""):
        achadas: list[tuple[int, str]] = []
        for m in _ALVO_RE.finditer(_marcar(sentenca)):
            if uf := _uf_do_alvo(m.group("alvo")):
                achadas.append((m.start(), uf))
        for m in _CIDADE_RE.finditer(" ".join(plano_de(sentenca).split())):
            achadas.append((m.start(), CIDADES_E_GENTILICOS[" ".join(m.group(1).split())]))
        for _pos, uf in sorted(achadas):
            if uf not in saida:
                saida.append(uf)
    return saida


def _trecho(sentenca: str) -> str | None:
    texto = " ".join(sentenca.split()).strip(" -–—*•·|_")
    if not texto or contato_fora_da_loja(texto):
        return None
    if len(texto) > MAX_CHARS_TRECHO:
        texto = texto[: MAX_CHARS_TRECHO - 1].rstrip(" ,;:-") + "…"
    return texto


def restricao_no_texto(*textos: str | None) -> tuple[list[str], str | None]:
    """(UFs restritas, a frase para citar) dos textos do anúncio (título, descrição). PURA.

    HTML vira linha (a mesma limpeza de tags da ficha). A frase é a primeira
    que restringe, sem contato fora da loja (com contato: sem trecho).
    """
    ufs: list[str] = []
    trecho: str | None = None
    for bruto in textos:
        if not isinstance(bruto, str) or not bruto.strip():
            continue
        texto = ficha_svc._TAG.sub(" ", ficha_svc._TAG_QUEBRA.sub("\n", bruto))
        for sentenca in _SENTENCAS.split(texto[:MAX_CHARS_DESCRICAO]):
            achadas = ufs_negadas(sentenca)
            if not achadas:
                continue
            for uf in achadas:
                if uf not in ufs:
                    ufs.append(uf)
            if trecho is None:
                trecho = _trecho(sentenca)
    return ufs, trecho


# Revisão L1–L3 (achado 6, 23/10/2026): a frase do anúncio que LIMITA o envio sem uma UF que
# o `ufs_negadas` leia (região, cidade, "somente para", "exceto") — a prova de "nenhuma
# restrição" (`restricao_do_anuncio(conferida=True)`) não sai com ela. Larga de propósito: o
# que ela pega a mais só tira a prova (pessoa confere), nunca afirma restrição.
_VERBO_DE_ENVIO = r"(?:envi|entreg|despach|mand|atend|vend|frete|cheg)\w*"
# Na frase, sozinhas: a negação do envio e o envio "somente/apenas para".
_LIMITA_ENVIO = re.compile(
    r"\b(?:nao|nem)\s+(?:\w+\s+){0,3}?" + _VERBO_DE_ENVIO
    + r"|\b" + _VERBO_DE_ENVIO + r"\s+(?:\w+\s+){0,3}?(?:somente|apenas|so|exclusivamente)\s+"
    r"(?:para|pra|no|na|nos|nas|em|ao|aos|o\s+estado|a\s+regiao)\b"
)  # fmt: skip
# Com o envio na MESMA frase: "somente/apenas para …".
_LIMITA_NA_FRASE = re.compile(
    r"\b(?:somente|apenas|exclusivamente)\s+(?:para|pra|no|na|nos|nas|em|ao|aos)\b"
)
# Com o envio em qualquer lugar da descrição: "exceto …", "região Norte".
_LIMITA_COM_ENVIO = re.compile(
    r"\bexceto\b|\bcom\s+excecao\b|\bmenos\s+(?:para|pra)\b"
    r"|\bregi(?:ao|oes)\s+(?:norte|nordeste|sul|sudeste|centro)"
)
_ENVIO_NA_DESCRICAO = re.compile(r"\b" + _VERBO_DE_ENVIO)


def limitacao_de_envio_nao_lida(*textos: str | None) -> bool:
    """O título ou a descrição têm frase que LIMITA o envio (`_LIMITA_ENVIO`; o "somente
    para" com o envio na frase; o "exceto"/a região com o envio na descrição)? PURA (só o
    `conferida` da liberação L3 a usa)."""
    for bruto in textos:
        if not isinstance(bruto, str) or not bruto.strip():
            continue
        texto = ficha_svc._TAG.sub(" ", ficha_svc._TAG_QUEBRA.sub("\n", bruto))
        plano_todo = " ".join(plano_de(texto[:MAX_CHARS_DESCRICAO]).split())
        if not _ENVIO_NA_DESCRICAO.search(plano_todo):
            continue
        for sentenca in _SENTENCAS.split(texto[:MAX_CHARS_DESCRICAO]):
            plano = " ".join(plano_de(sentenca).split())
            if not plano:
                continue
            if _LIMITA_ENVIO.search(plano) or _LIMITA_COM_ENVIO.search(plano):
                return True
            if _LIMITA_NA_FRASE.search(plano) and _ENVIO_NA_DESCRICAO.search(plano):
                return True
    return False


def regra_apple_shopee(plataforma: Any, titulo: str | None) -> list[str]:
    """A regra fixa do faturamento (Shopee + Apple → RJ) no título do anúncio. PURA.

    As palavras e a UF são as de `nf_auto_enfileirar` (lá o nome do item do
    pedido, aqui o título; o mesmo "contém", sem caixa).
    """
    plat = str(getattr(plataforma, "value", plataforma) or "").lower()
    if plat not in _RESTRICAO_PLATAFORMAS or not titulo:
        return []
    nome = titulo.lower()
    return [_RESTRICAO_UF] if any(kw in nome for kw in _RESTRICAO_KEYWORDS) else []


# ── A loja (store_info) ───────────────────────────────────────────────────


@dataclass(frozen=True)
class RestricaoLoja:
    """Uma ficha da loja (tela Lojas) com Restrição E exceções: o que o faturamento avalia.

    Nunca vai ao modelo (as exceções têm valor em R$).
    """

    ufs: frozenset[str]
    regras: tuple[dict, ...]


def _ufs(bruto: Any) -> frozenset[str]:
    if not isinstance(bruto, list | tuple):
        return frozenset()
    return frozenset(u for x in bruto if (u := str(x or "").strip().upper()) in UFS)


async def restricao_da_loja(
    session: AsyncSession, integration_id: UUID | None
) -> list[RestricaoLoja]:
    """As fichas da loja da integração com Restrição de UF e exceções — [] = nenhuma.

    Sem exceção válida a ficha é inerte no faturamento (`avaliar_restricao_loja`):
    fica de fora aqui também. Ligação: ver o topo.
    """
    if integration_id is None:
        return []
    integracao = await session.get(Integration, integration_id)
    blings = {
        str(b)
        for b in (
            await session.scalars(
                select(Store.bling_store_id).where(
                    Store.integration_id == integration_id, Store.bling_store_id.is_not(None)
                )
            )
        ).all()
    }
    if integracao is not None and integracao.bling_loja_id is not None:
        blings.add(str(integracao.bling_loja_id))
    condicao = StoreInfo.integration_id == integration_id
    if blings:
        condicao = or_(condicao, StoreInfo.bling_store_id.in_(sorted(blings)))
    linhas = (
        await session.execute(
            select(StoreInfo.uf_restrictions, StoreInfo.excecoes)
            .where(condicao, StoreInfo.archived_at.is_(None))
            .order_by(StoreInfo.created_at, StoreInfo.id)
        )
    ).all()
    saida: list[RestricaoLoja] = []
    for ufs_brutas, excecoes in linhas:
        ufs = _ufs(ufs_brutas)
        regras = tuple(r for r in (excecoes or []) if isinstance(r, dict))
        if ufs and regras:
            saida.append(RestricaoLoja(ufs, regras))
    return saida


def _avaliar(r: RestricaoLoja, uf: str, valor: Any, itens: list[tuple[str | None, str | None]]):
    return avaliar_restricao_loja(
        list(r.regras), uf_destino=uf, ufs_restricao=sorted(r.ufs), valor_total=valor, itens=itens
    )


def avaliar_loja_no_anuncio(
    restricoes: Sequence[RestricaoLoja],
    itens: list[tuple[str | None, str | None]],
    precos: Sequence[float],
) -> tuple[list[str], list[str]]:
    """(UFs restritas, UFs que dependem do valor do pedido) para um anúncio. PURA.

    `itens` = (sku, nome) dos produtos do anúncio; `precos` = os preços
    conhecidos (vazio = não se sabe). A avaliação é a do faturamento: sem
    valor (None) a regra de valor libera, com valor infinito ela bloqueia —
    a diferença diz se só o valor decide. Só o valor decide: todo preço
    bloqueia → restrita; senão → conforme o pedido (o preço do cartão é o
    MENOR das variações e o faturamento olha o total: o estado nunca some).
    """
    restritas: list[str] = []
    conforme: list[str] = []
    for r in restricoes:
        for uf in sorted(r.ufs):
            if _avaliar(r, uf, float("inf"), itens) is None:
                continue  # exceção de palavra/sku casa: o anúncio vai
            if _avaliar(r, uf, None, itens) is not None:
                destino = restritas  # nenhuma exceção casa, qualquer que seja o valor
            elif precos and all(_avaliar(r, uf, p, itens) is not None for p in precos):
                destino = restritas
            else:
                destino = conforme
            if uf not in destino:
                destino.append(uf)
    return restritas, [u for u in conforme if u not in restritas]


# ── O anúncio (banco + Redis) ─────────────────────────────────────────────


async def _textos_do_anuncio(
    session: AsyncSession,
    plataforma: IntegrationPlatform,
    integration_id: UUID | None,
    item_id: str,
    titulo: str | None,
) -> tuple[str | None, str | None]:
    """(título, descrição CRUA) do anúncio: a ficha fresca do Redis, senão o catálogo."""
    fresca = await ficha_svc._ficha_fresca(plataforma, integration_id, item_id)
    if fresca is not None and (fresca.get("descricao") or fresca.get("raw")):
        descricao = fresca.get("descricao")
        if not (isinstance(descricao, str) and descricao.strip()):
            descricao = ficha_svc._descricao_do_raw(ficha_svc._base(fresca.get("raw")))
        return fresca.get("titulo") or titulo, descricao
    for nome, descricao, raw, _plat, _integ in await ficha_svc._do_catalogo(
        session, plataforma, item_id, integration_id
    ):
        if titulo and not ficha_svc.mesmo_titulo(nome, titulo):
            continue
        texto = descricao if isinstance(descricao, str) and descricao.strip() else None
        return titulo or nome, texto or ficha_svc._descricao_do_raw(ficha_svc._base(raw))
    return titulo, None


async def _itens_e_precos(
    session: AsyncSession,
    plataforma: IntegrationPlatform,
    integration_id: UUID | None,
    item_id: str,
) -> tuple[list[tuple[str | None, str | None]], list[float]]:
    """Os (sku, nome) dos produtos vinculados e os preços conhecidos do anúncio."""
    itens: list[tuple[str | None, str | None]] = []
    precos: list[float] = []
    if integration_id is not None:
        for sku, nome, preco in (
            await session.execute(
                select(Product.sku, Product.name, ProductLink.price)
                .join(Product, Product.id == ProductLink.product_id)
                .where(
                    ProductLink.integration_id == integration_id,
                    ProductLink.platform == plataforma,
                    ProductLink.external_id == item_id,
                    ProductLink.morto_desde.is_(None),
                )
                .order_by(ProductLink.created_at)
                .limit(MAX_VINCULOS)
            )
        ).all():
            itens.append((sku, nome))
            if preco is not None and float(preco) > 0:
                precos.append(float(preco))
        # O cartão do anúncio (o enriquecimento grava o preço da loja).
        from app.services.atendimento import enriquecer

        try:
            bruto = await ficha_svc.redis.get(
                enriquecer.chave_produto(plataforma.value, integration_id, item_id)
            )
            cartao = json.loads(bruto) if bruto else {}
        except Exception as exc:  # noqa: BLE001 — sem o cartão, fica com os vínculos
            logger.info("atendimento_restricao_cartao_indisponivel", erro=type(exc).__name__)
            cartao = {}
        preco = cartao.get("preco") if isinstance(cartao, dict) else None
        if isinstance(preco, int | float) and not isinstance(preco, bool) and preco > 0:
            precos.append(float(preco))
    return itens, precos


async def restricao_do_anuncio(
    session: AsyncSession,
    plataforma: IntegrationPlatform,
    integration_id: UUID | None,
    item_id: str,
    titulo: str | None,
    *,
    conferida: bool = False,
) -> dict | None:
    """A restrição de envio de UM anúncio da loja da conversa — ou None (nenhuma).

    `conferida` (liberação L3 do dono, 23/10/2026; só o agente: o pacote e o
    `ver_anuncio`) = o resultado diz o que foi conferido: com restrição, a chave
    "conferido" (as três fontes); SEM restrição e com a descrição do anúncio lida,
    `{"ufs": [], "ufs_conforme_pedido": [], "nenhuma": True, "conferido": [...]}` — a
    prova de que nem o anúncio, nem a regra Apple→RJ, nem a loja restringem (o "Enviamos
    para todo o Brasil" só sai com ela). Sem a descrição lida (Redis e catálogo sem o
    anúncio), None como antes: não se sabe.

    `{"ufs": [...], "fonte": "anuncio"|"regra_apple_shopee"|"loja",
    "fontes": [...], "trecho": str|None, "ufs_conforme_pedido": [...]}`:
    `ufs` = os estados que não recebem (a união das fontes); `fonte` = a
    primeira na ordem anúncio → regra → loja; `trecho` = a frase do anúncio
    para citar (só com a fonte "anuncio"); `ufs_conforme_pedido` = os da
    loja que dependem do valor do pedido. Nunca valor, nunca a UF do
    comprador. Erro de banco sobe; Redis fora do ar é "sem a ficha fresca".
    """
    titulo_atual, descricao = await _textos_do_anuncio(
        session, plataforma, integration_id, item_id, titulo
    )
    por_fonte: dict[str, list[str]] = {}
    do_anuncio, trecho = restricao_no_texto(titulo_atual, descricao)
    if do_anuncio:
        por_fonte[FONTE_ANUNCIO] = do_anuncio
    if apple := regra_apple_shopee(plataforma, titulo_atual or titulo):
        por_fonte[FONTE_REGRA_APPLE] = apple
    conforme: list[str] = []
    restricoes = await restricao_da_loja(session, integration_id)
    if restricoes:
        itens, precos = await _itens_e_precos(session, plataforma, integration_id, item_id)
        if not itens and (titulo_atual or titulo):
            itens = [(None, titulo_atual or titulo)]
        da_loja, conforme = avaliar_loja_no_anuncio(restricoes, itens, precos)
        if da_loja:
            por_fonte[FONTE_LOJA] = da_loja
    ufs = sorted({u for lista in por_fonte.values() for u in lista})
    conforme = sorted(u for u in conforme if u not in ufs)
    if not ufs and not conforme:
        # Revisão L1–L3 (achado 6, 23/10/2026): a frase de envio negada ou limitada que o
        # leitor não converteu em UF ("não enviamos para a região Norte", "somente para SP",
        # "não pode ser enviado para Manaus") — não se sabe: sem a prova.
        if (
            conferida
            and isinstance(descricao, str)
            and descricao.strip()
            and not limitacao_de_envio_nao_lida(titulo_atual, descricao)
        ):
            return {
                "ufs": [],
                "ufs_conforme_pedido": [],
                "nenhuma": True,
                "conferido": list(_ORDEM_FONTES),
                "resumo": RESUMO_NENHUMA,
            }
        return None
    fontes = [f for f in _ORDEM_FONTES if f in por_fonte] or [FONTE_LOJA]
    saida = {
        "ufs": ufs,
        "fonte": fontes[0],
        "fontes": fontes,
        "trecho": trecho if FONTE_ANUNCIO in por_fonte else None,
        "ufs_conforme_pedido": conforme,
    }
    if conferida:
        saida["conferido"] = list(_ORDEM_FONTES)
        saida["resumo"] = resumo_da_restricao(ufs, conforme)
    return saida


# Rodada "fechar o eval" (23/10/2026, 0bd8541e29d9 — "vocês fazem entrega para Maceió?"):
# com a restrição só da loja ("fonte": "loja", "ufs": [], "ufs_conforme_pedido": ["RJ"]),
# o agente leu "sem lista de estados" e foi para pessoa (a equipe: "sim enviamos para todo
# brasil!"). O `resumo` diz a regra inteira em uma frase (só o agente: `conferida`); o
# valor do limite nunca vai (revisão de 20/10/2026).
RESUMO_NENHUMA = "nenhuma restrição: todos os estados recebem"


def resumo_da_restricao(ufs: Sequence[str], conforme: Sequence[str]) -> str:
    """A restrição em uma frase (ver acima). PURA."""
    partes = []
    if ufs:
        partes.append(f"não envia para {', '.join(ufs)}")
    if conforme:
        partes.append(
            f"{', '.join(conforme)}: depende do valor do pedido (não diga sim nem não para "
            f"{'esse estado' if len(conforme) == 1 else 'esses estados'})"
        )
    if not partes:
        return RESUMO_NENHUMA
    return "; ".join(partes) + "; todos os outros estados recebem"


def sem_restricao_conferida(restricao: Any) -> bool:
    """O bloco `restricao_envio` é a prova de "nenhuma restrição" (`conferida`)? PURA."""
    return (
        isinstance(restricao, dict)
        and restricao.get("nenhuma") is True
        and not restricao.get("ufs")
        and not restricao.get("ufs_conforme_pedido")
    )


# ── O pedido (pós-venda) ──────────────────────────────────────────────────


async def pedido_para_uf_restrita(
    session: AsyncSession,
    *,
    numero: str | None = None,
    numeroloja: str | None = None,
    ufs_do_anuncio: Iterable[str] = (),
) -> bool:
    """O pedido (número do Bling ou da loja) vai para um estado que não recebe? Só True/False.

    As regras do faturamento sobre as linhas do pedido no Bling: Shopee +
    item Apple + RJ; a restrição da loja (`avaliar_restricao_loja`, com o
    valor do pedido); e as UFs do anúncio (`ufs_do_anuncio`, de
    `restricao_do_anuncio`). Pedido sem UF de destino ou não achado: False.
    A UF nunca sai daqui.
    """
    if not numero and not numeroloja:
        return False
    condicao = BlingOrder.numero == numero if numero else BlingOrder.numeroloja == numeroloja
    linhas = (
        await session.execute(
            select(
                BlingOrder.numero,
                BlingOrder.loja,
                BlingOrder.uf_destino,
                BlingOrder.valorbase,
                BlingOrder.item_codigo,
                BlingOrder.item_descricao,
            )
            .where(condicao)
            .order_by(BlingOrder.numero, BlingOrder.item_index)
            .limit(MAX_VINCULOS)
        )
    ).all()
    if not linhas:
        return False
    # Um pedido só (o número da loja pode repetir em pedidos desmembrados: o primeiro).
    primeiro = linhas[0].numero
    linhas = [r for r in linhas if r.numero == primeiro]
    uf = next(((r.uf_destino or "").strip().upper() for r in linhas if r.uf_destino), "")
    if not uf:
        return False
    if uf in {str(u).strip().upper() for u in ufs_do_anuncio}:
        return True
    loja = next((r.loja for r in linhas if r.loja), None)
    if loja is None:
        return False
    fichas = (
        await session.execute(
            select(StoreInfo.platform, StoreInfo.uf_restrictions, StoreInfo.excecoes).where(
                StoreInfo.bling_store_id == str(loja)
            )
        )
    ).all()
    itens = [(r.item_codigo, r.item_descricao) for r in linhas]
    for plataforma, ufs_restricao, excecoes in fichas:
        if (
            str(plataforma or "").lower() in _RESTRICAO_PLATAFORMAS
            and uf == _RESTRICAO_UF
            and any(
                nome and any(kw in nome.lower() for kw in _RESTRICAO_KEYWORDS) for _, nome in itens
            )
        ):
            return True
        regras = [r for r in excecoes if isinstance(r, dict)] if isinstance(excecoes, list) else []
        if regras and avaliar_restricao_loja(
            regras,
            uf_destino=uf,
            ufs_restricao=list(ufs_restricao or []),
            valor_total=linhas[0].valorbase,
            itens=itens,
        ):
            return True
    return False
