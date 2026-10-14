"""A FICHA DO ANÚNCIO nos FATOS da IA (14/10/2026).

O dono: "não dá pra pegar a ficha do produto? … tem que ser pra todos os
possíveis: Shopee, TikTok, ML, tudo que der, busca a ficha caso seja
linkado com algum produto". O caso: na Shopee da Kia o comprador mandou o
cartão do "Uranyx A17 Pro Max" e perguntou se tem NFC; a IA só tinha o
TÍTULO e mandou para pessoa.

QUAIS ANÚNCIOS (`anuncios_da_conversa`, no máximo `MAX_ANUNCIOS_FICHA`, o
mais recente primeiro), cada um com o título ATUAL quando a conversa o tem:
  1. o cartão de produto que o CLIENTE mandou (anexo `produto` das mensagens
     recentes), do mais novo para o mais velho — o título é o do cartão;
  2. o anúncio da conversa: `conversa.anuncio_id` / `dados.item_id` /
     `dados.produto.item_id` (a pergunta do ML, o produto de onde o
     comprador abriu o chat da Shopee/TikTok, o da avaliação/reclamação);
  3. os itens do pedido da conversa (`dados.pedido_mkt.itens[].item_id`).
O PRIMEIRO sem ficha encerra a lista: com o cartão novo fora do catálogo, a
ficha de um anúncio mais velho responderia sobre o produto errado ("tem
NFC?" do cartão de agora, com a ficha do anúncio de ontem).

DE ONDE (`ficha_do_anuncio`), nesta ordem:
  1. a ficha FRESCA: o que a própria loja devolveu na ida que o
     enriquecimento JÁ faz para montar o cartão de produto (`get_item_base_info`
     da Shopee: descrição + `attribute_list`; `/items` do ML: `attributes`),
     ou na busca sob demanda (item 3), guardada no Redis por `CACHE_FICHA_S`
     (`chave_ficha`), à parte do cartão (o cartão vai para a tela; a ficha,
     só para a IA). Marcada com a `fonte` ("loja (Shopee), agora" — sem
     número: a conferência de número inventado lê os FATOS). A do ML sem a
     descrição ganha a descrição pela busca sob demanda (`ficha_loja.py`).
  2. o NOSSO catálogo (`listings`, pela plataforma e pelo id do anúncio, a
     da própria loja primeiro) — só a linha importada há até
     `MAX_DIAS_CATALOGO_FICHA` dias e, com o título atual conhecido, com o
     MESMO título. A importação do catálogo é manual (Anúncios → importar);
     em 14/10/2026 a última era de maio, e na Kia o anúncio do cartão já se
     chamava "… S5 Premium NFC" enquanto a descrição de maio dizia "NFC -
     Não Tem": a ficha velha respondia errado, com cara de certa.
  3. a LOJA, sob demanda (14/10/2026, o dono: "se necessário acessa o
     produto na loja pra pegar os dados" — `ficha_loja.py`): sem ficha
     fresca nem catálogo válido, a sugestão (cron e "Sugerir") busca o
     anúncio na loja da conversa — Shopee, ML e TikTok —, com tempo curto,
     cota e cache (positivo na chave da fresca; negativo curto). Falhou ou
     demorou: segue para o item 4 (ou sem ficha), como antes.
  4. o mesmo PRODUTO em outro anúncio: `product_links` (anúncio → produtos
     do Bling → os anúncios desses produtos) → `listings` (com o mesmo
     corte de data). Vínculo morto não conta, nem na origem nem no destino,
     e o outro anúncio só vale se TODOS os produtos vivos dele forem
     produtos deste — o anúncio da Shopee que junta o A17 e o A18 como
     variações não é ficha do anúncio do A18 no TikTok. A ficha vem com
     `fonte` ("outro anúncio do mesmo produto") e o título dos dois
     anúncios; o manual não a aceita para "o que acompanha" (kit varia por
     anúncio).

COBERTURA POR PLATAFORMA (produção, 14/10/2026):
  - Shopee: ficha fresca de todo cartão de produto que o cliente manda (o
    enriquecimento já lê o `get_item_base_info`: descrição + atributos); o
    anúncio da conversa e os itens do pedido sem cartão, pela busca sob
    demanda (o mesmo `get_item_base_info`). Catálogo (737 anúncios,
    descrição + `attribute_list`, 43 dentro de `raw_data.item`): só depois
    de importar de novo (o de maio não passa no corte). O id = `item_id`.
  - Mercado Livre: ficha fresca na pergunta (o cartão do anúncio perguntado)
    e nos itens do pedido (o retrato do pós-venda já lê o `/items`, agora
    com `attributes`), SEM descrição (o `/items` não traz o texto): a
    descrição vem da busca sob demanda (`/items/{id}/description`), e o
    anúncio sem fresca, inteiro (`/items/{id}` + descrição). Catálogo
    (3.723, `raw_data.attributes`): idem, só reimportado. O id = MLB….
  - TikTok: sem `listings`; a busca sob demanda lê o produto
    (`/product/202309/products/{id}`, com o shop_cipher da loja da
    conversa); sem ela, o mesmo produto em outro anúncio (`product_links`
    tiktok → Shopee/ML), com catálogo reimportado. O id = `product_id`.
  - Magalu: o mesmo produto em outro anúncio (o `anuncio_id` da conversa é
    o SKU, o mesmo `external_id` do `product_links` magalu); a API da
    Magalu não tem a ficha (o portfólio é SKU, título e status).
  - Amazon: `listings` só com título/SKU/ASIN — sem ficha (e o e-mail da
    Amazon não liga anúncio).
  - Temu, AliExpress, redes e site: sem catálogo nem API — sem ficha.

O QUE ENTRA (compacto, `montar_ficha`): título; os atributos PREENCHIDOS
("nome: valor", até `MAX_ATRIBUTOS_FICHA`, os que batem com a pergunta do
cliente primeiro — o ML tem até 125); e a descrição LIMPA (`limpar_descricao`):
sem HTML, sem símbolo/emoji, espaços normalizados, e SEM as linhas que a IA
não pode usar — a LINHA com contato fora da loja (as regras de `validador.
contato_fora_da_loja`, na linha inteira: "lojakia. com. br" partido em
frases também sai), garantia e defeito, preço/frete/prazo de envio,
devolução, troca, nota fiscal, avaliação, "filmado e pesado", condição e
originalidade ("novo", "lacrado", "original"), homologação/Anatel/Inmetro e
RESISTÊNCIA ("resistente", IP68, "à prova d'água/de poeira", profundidade,
submersão, queda, choque) —, cortada em `MAX_CHARS_DESCRICAO_FICHA`. Os
mesmos assuntos saem dos atributos, pelo NOME (garantia, condição,
SKU/GTIN, embalagem, homologação, origem, fabricante, resistência) e pelo
VALOR ("Estado: Novo e lacrado", "Tipo: Original", "À Prova de Poeira").
Condição, originalidade e resistência continuam SÓ pelo título (regra de
segurança 8 do manual): a ficha não é fonte de "novo/lacrado/original" nem
de "resiste à água". O bloco inteiro cabe em `MAX_CHARS_FICHAS`, o segundo
anúncio em `MAX_CHARS_SEGUNDA_FICHA` (`caber`): corta a descrição do último
anúncio, depois os atributos dele, depois o anúncio.

No AUTOMÁTICO, a resposta de assunto de dado do produto
(`CATEGORIAS_DA_FICHA`) com a ficha nos FATOS não sai sozinha: vai para
pessoa (`ia.MOTIVO_FICHA_NO_AUTOMATICO`). Liberar é decisão do dono.

Nunca dado de comprador: o texto do cliente só ORDENA os atributos (as
palavras da pergunta), não entra na ficha.
"""

from __future__ import annotations

import html
import json
import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID

import structlog
from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models import AtendimentoConversa, AtendimentoMensagem, Listing, ProductLink
from app.models.enums import IntegrationPlatform, ListingStatus
from app.redis_client import redis
from app.services.atendimento import validador
from app.services.atendimento.constantes import AUTOR_CLIENTE, CANAL_PERGUNTA

if TYPE_CHECKING:
    from app.services.atendimento.ficha_loja import Busca

logger = structlog.get_logger()

# ── Tetos ─────────────────────────────────────────────────────────────────

MAX_ANUNCIOS_FICHA = 2
MAX_ATRIBUTOS_FICHA = 25
MAX_CHARS_ATRIBUTO = 80  # a linha "nome: valor"
MAX_CHARS_TITULO_FICHA = 160
MAX_CHARS_DESCRICAO_FICHA = 1_200
# O bloco inteiro (JSON, até 2 anúncios). Vai na MENSAGEM (FATOS), fora do
# orçamento do prompt de sistema (`ia.MAX_CHARS_RESPOSTA`): não tira
# correção da equipe nem regra — tem o teto próprio. O segundo anúncio tem
# um teto menor: a média era ~6,2 mil tokens de entrada por sugestão (Groq,
# 14/10/2026), e o anúncio mais recente é o que o cliente perguntou.
MAX_CHARS_FICHAS = 2_500
MAX_CHARS_SEGUNDA_FICHA = 1_000
# No corte (`caber`), os atributos do anúncio descem até este piso antes
# de o anúncio sair.
MIN_ATRIBUTOS_NO_CORTE = 5
# Linhas do catálogo lidas por anúncio (a da loja, a ativa, a mais nova).
MAX_LINHAS_CATALOGO = 3

# ── Frescor ───────────────────────────────────────────────────────────────

# O catálogo só vale importado há até tantos dias (`listings.imported_at`, que
# toda importação renova). Velho, a ficha sai — melhor pessoa que resposta
# errada com cara de certa (o "NFC - Não Tem" de maio, ver o topo).
MAX_DIAS_CATALOGO_FICHA = 30
# A ficha fresca no Redis (gravada pelo enriquecimento, junto com o cartão).
CACHE_FICHA_S = 3 * 24 * 3600
# A descrição crua guardada (a limpeza corta em `MAX_CHARS_DESCRICAO_FICHA`
# depois de tirar as linhas proibidas, então guarda-se mais).
MAX_CHARS_DESCRICAO_BRUTA = 8_000

# Assuntos de DADO DO PRODUTO: no automático, a resposta com a ficha nos
# FATOS vai para pessoa (`ia._gerar`). `duvida_produto` é o assunto das
# constantes (sem manual base no banco).
CATEGORIAS_DA_FICHA = frozenset(
    {
        "o_que_e_o_anuncio",
        "espec_celular",
        "espec_mala",
        "espec_eletro",
        "espec_outros",
        "conteudo_da_caixa",
        "duvida_produto",
    }
)

DE_ONDE_CARTAO = "cartão de produto que o cliente mandou"
DE_ONDE_PERGUNTA = "anúncio da pergunta"
DE_ONDE_ANUNCIO = "anúncio ligado à conversa"
DE_ONDE_PEDIDO = "item do pedido da conversa"
FONTE_OUTRO_ANUNCIO = "outro anúncio do mesmo produto"
# A ficha que a própria loja devolveu (`fonte_da_loja`): "loja (Shopee), agora".
FONTE_LOJA = "loja"
# A idade da ficha da loja, SEM número (`fonte_da_loja`).
QUANDO_AGORA = "agora"
QUANDO_HOJE = "há menos de um dia"
QUANDO_DIAS = "há até três dias"

_TIPO_PRODUTO = "produto"  # o anexo do cartão de produto (shopee.py, tiktok.py)

_NOME_PLATAFORMA = {
    IntegrationPlatform.SHOPEE: "Shopee",
    IntegrationPlatform.ML: "Mercado Livre",
    IntegrationPlatform.TIKTOK: "TikTok Shop",
    IntegrationPlatform.AMAZON: "Amazon",
    IntegrationPlatform.MAGALU: "Magalu",
}
_PLATAFORMAS = {p.value: p for p in IntegrationPlatform if p is not IntegrationPlatform.BLING}


# ── Conversões ────────────────────────────────────────────────────────────


def _texto(valor: Any) -> str | None:
    """Texto limpo, ou None para vazio. Número vira texto (ids da Shopee são int)."""
    if valor is None or isinstance(valor, bool):
        return None
    if isinstance(valor, int | float):
        valor = str(valor)
    if not isinstance(valor, str):
        return None
    valor = " ".join(valor.split())
    return valor or None


def _lista(bruto: Any) -> list[dict]:
    return [x for x in bruto if isinstance(x, dict)] if isinstance(bruto, list) else []


def _dict(bruto: Any) -> dict:
    return bruto if isinstance(bruto, dict) else {}


def _plano(texto: str) -> str:
    return validador.plano_de(texto)


def _cortar(texto: str, teto: int) -> str:
    """O texto em até `teto` caracteres, cortado na palavra; "…" marca o corte."""
    if len(texto) <= teto:
        return texto
    if teto < 2:
        return ""
    espaco = texto[: teto - 1].rfind(" ")
    # Palavra enorme no fim ("Obs: aaaa…"): corta nela, não na metade de cima.
    corte = texto[:espaco] if espaco >= teto // 2 else texto[: teto - 1]
    return corte.rstrip(" ,;:.-") + "…"


def _cortar_linhas(texto: str, teto: int) -> str:
    """O texto em até `teto` caracteres, só com linhas INTEIRAS (+ "…").

    Nem a primeira linha cabe: ela cortada na palavra. Teto < 2: "".
    """
    if len(texto) <= teto:
        return texto
    if teto < 2:
        return ""
    linhas = texto.split("\n")
    saida: list[str] = []
    for linha in linhas:
        if len("\n".join([*saida, linha])) + 1 > teto:
            break
        saida.append(linha)
    if saida:
        return "\n".join(saida) + "…"
    return _cortar(linhas[0], teto)


# ── Quais anúncios ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Anuncio:
    item_id: str
    de_onde: str
    # O título ATUAL do anúncio, quando a conversa o tem (o do cartão, o da
    # conversa, o do item do pedido): mostra ao modelo de que anúncio é a
    # ficha e confere a linha do catálogo (`mesmo_titulo`).
    titulo: str | None = None


def plataforma_do_catalogo(plataforma: str | None) -> IntegrationPlatform | None:
    """A plataforma do catálogo (`listings`/`product_links`) da conversa, ou None.

    Instagram, site, AliExpress: o `anuncio_id` deles não é anúncio de loja
    (no Instagram é a mídia do post).
    """
    return _PLATAFORMAS.get(str(plataforma or ""))


def anuncios_da_conversa(
    conversa: AtendimentoConversa, mensagens: Sequence[AtendimentoMensagem]
) -> list[Anuncio]:
    """Os anúncios ligados à conversa, o mais recente primeiro, sem repetir. PURA.

    `mensagens` = as recentes, da mais antiga para a mais nova (`ia._gerar`).
    O anúncio repetido fica no lugar do primeiro, com o primeiro título que
    aparecer (o cartão sem título ainda — a loja não respondeu — ganha o da
    conversa).
    """
    if plataforma_do_catalogo(conversa.plataforma) is None:
        return []
    saida: list[Anuncio] = []

    def _por(item: Any, de_onde: str, titulo: Any = None) -> None:
        item_id = _texto(item)
        if not item_id or len(item_id) > 64:
            return
        titulo = _texto(titulo)
        for i, a in enumerate(saida):
            if a.item_id == item_id:
                if a.titulo is None and titulo:
                    saida[i] = Anuncio(item_id, a.de_onde, titulo)
                return
        saida.append(Anuncio(item_id, de_onde, titulo))

    for m in reversed(list(mensagens)):
        if m.autor != AUTOR_CLIENTE:
            continue
        for anexo in _lista(m.anexos):
            if anexo.get("tipo") == _TIPO_PRODUTO:
                _por(anexo.get("item_id"), DE_ONDE_CARTAO, anexo.get("titulo"))
    dados = _dict(conversa.dados)
    produto = _dict(dados.get("produto"))
    de_onde = DE_ONDE_PERGUNTA if conversa.canal == CANAL_PERGUNTA else DE_ONDE_ANUNCIO
    titulo = conversa.anuncio_titulo or produto.get("titulo")
    _por(conversa.anuncio_id, de_onde, titulo)
    _por(dados.get("item_id"), de_onde, titulo)
    _por(produto.get("item_id"), de_onde, produto.get("titulo"))
    for item in _lista(_dict(dados.get("pedido_mkt")).get("itens")):
        _por(item.get("item_id"), DE_ONDE_PEDIDO, item.get("titulo"))
    return saida


_SEPARA_TITULO = re.compile(r"[a-z0-9]+")


def _chave_titulo(titulo: Any) -> str:
    return " ".join(_SEPARA_TITULO.findall(_plano(_texto(titulo) or "")))


def mesmo_titulo(do_catalogo: Any, atual: Any) -> bool:
    """O título do catálogo é o título atual do anúncio? PURA.

    Sem acento, caixa nem pontuação. A linha da VARIAÇÃO no catálogo da
    Shopee ("Título - Prata,12 GB / 128 GB") vale para o título do anúncio.
    """
    catalogo, agora = _chave_titulo(do_catalogo), _chave_titulo(atual)
    if not catalogo or not agora:
        return False
    return catalogo == agora or catalogo.startswith(agora + " ")


# ── A ficha de UM anúncio (pura) ──────────────────────────────────────────

# Atributo que a IA não pode usar (ou que só confunde): garantia, condição,
# código interno, embalagem, homologação, origem/fabricante, resistência,
# preço/frete/prazo. Sobre o nome (sem acento, minúsculo) e o id do ML.
_ATRIBUTO_FORA = re.compile(
    r"garantia|warrant|\bcondicao\b|\bcondition\b|\bsku\b|gtin|\bean\b|\bupc\b"
    r"|codigo universal|registr|homolog|anatel|inmetro|certific|\bncm\b"
    r"|embalagem|package|\borigem\b|\borigin\b|fabricante|manufacturer|importad"
    r"|plano de troca|trade in|hazmat|transportabil|regalavel|giftable"
    r"|prova d|waterproof|resist|ip rating|classificacao ip"
    r"|robust|rugged|preco|price|desconto|frete|shipping|envio|prazo|days to ship"
    r"|pre ?order|\bestado\b"
)
_ATRIBUTO_SEM_VALOR = frozenset({"", "-", "n/a", "na", "null", "none", "nobrand", "no brand", "0"})
_NAO = frozenset({"nao", "no"})

# Linha (ou frase) da descrição que a IA não pode usar — ver o topo.
_DESCRICAO_FORA = re.compile(
    r"garantia|warrant|defeito|conserto|devolu|reembols|estorn|\btroca|\btrocar|arrependi"
    # "cancelamento de ruído" (ANC do fone) é dado do produto.
    r"|\bcancel(?!amento\s+(?:ativo\s+)?(?:de\s+)?(?:ruido|eco|ativo))"
    r"|frete|\benvi[oa]\b|\benviad|\benviamos|\bpostag|\bpostad|despach|transportadora"
    r"|correios|\bprazo|dias?\s+uteis|pronta\s+entrega"
    r"|r\s*\$|\breais\b|%|desconto|cupo[mn]|promoc|\boferta"
    r"|avalia|estrela|feedback"
    r"|filmad|pesad|golpe|fraude"
    r"|nota\s+fiscal|\bnfe?\b"
    r"|lacrad|original|recondicion|seminov|\busad[oa]s?\b|vitrine|replica|primeira\s+linha"
    r"|\b(?:produto|aparelho|item|celular)\s+nov[oa]s?\b|\bnov[oa]s?\s+(?:e\s+)?(?:lacrad|na\s+caixa"
    r"|sem\s+uso|de\s+fabrica)"
    r"|homolog|anatel|inmetro|certific|prova\s+d|indestrut"
    r"|importad|fabricante|fabricad[oa]s?\s+(?:na|no|pel[oa])\b|assistencia"
    # Resistência (regra de segurança 8: só pelo título). Medida pura fica:
    # "Profundidade: 8 mm" não é "1,5 metros de profundidade".
    r"|resisten(?:te|tes|cia|cias)\b|\bip\s?-?[0-9x]{2}k?\b|\bipx\s?\d"
    r"|metros?\s+de\s+profundidade|\d\s*m\s+de\s+profundidade|\b\d+\s*atm\b"
    r"|submer|mergulh|piscina|debaixo\s+d|embaixo\s+d|\bchuva"
    r"|queda|choque|impacto|militar|mil\s?-?\s?std|waterproof|water\s*resist|dust\s*proof"
    r"|shock\s*proof|rugged|robust"
)
# O VALOR do atributo que a IA não pode usar: o mesmo da descrição e a
# condição sozinha ("Estado: Novo", "Tipo: Original").
_VALOR_SO_CONDICAO = frozenset(
    {
        "novo",
        "nova",
        "novos",
        "novas",
        "new",
        "usado",
        "usada",
        "used",
        "seminovo",
        "seminova",
        "recondicionado",
        "recondicionada",
        "refurbished",
        "lacrado",
        "lacrada",
        "original",
        "originais",
        "vitrine",
    }
)
_FIM_DE_FRASE = re.compile(r"(?<=[.!?;])\s+")
_TAG_QUEBRA = re.compile(r"<\s*/?\s*(?:br|p|div|li|ul|ol|tr|h[1-6])\b[^>]{0,200}>", re.IGNORECASE)
_TAG = re.compile(r"<[^<>]{0,500}>")
_PONTAS = " \t-–—*•·|_=~>"


def _sem_simbolos(texto: str) -> str:
    """Sem emoji nem símbolo gráfico (✅ ★ ➤): categoria Unicode "So"/"Cs"/"Co"."""
    return "".join(
        " " if unicodedata.category(ch) in ("So", "Cs", "Co", "Cn") else ch for ch in texto
    )


def _frase_serve(frase: str) -> bool:
    return not (validador.contato_fora_da_loja(frase) or _DESCRICAO_FORA.search(_plano(frase)))


def _valor_serve(valor: str) -> bool:
    """O valor de um atributo pode ir para o modelo? (condição, resistência, contato: não)"""
    plano = _plano(valor).strip(" .")
    return not (
        plano in _VALOR_SO_CONDICAO
        or _DESCRICAO_FORA.search(plano)
        or validador.contato_fora_da_loja(valor)
    )


def limpar_descricao(texto: Any, teto: int = MAX_CHARS_DESCRICAO_FICHA) -> str | None:
    """A descrição do anúncio pronta para o modelo — ou None se não sobrar nada. PURA.

    Sem HTML (as quebras viram linha), sem símbolo/emoji, espaços
    normalizados; linha a linha: a LINHA com contato fora da loja sai
    inteira (o contato partido em frases, "lojakia. com. br", só aparece na
    linha toda); dentro da linha, frase a frase, sai a frase com contato ou
    com assunto que a IA não pode usar (`_DESCRICAO_FORA`). Cortada em
    `teto`, em linhas inteiras. Sobrou contato no texto todo (partido entre
    linhas): sem descrição.
    """
    bruto = _texto(texto) if not isinstance(texto, str) else texto
    if not bruto:
        return None
    t = html.unescape(bruto)
    t = _TAG_QUEBRA.sub("\n", t)
    t = _TAG.sub(" ", t)
    t = unicodedata.normalize("NFC", t).replace("\r\n", "\n").replace("\r", "\n")
    linhas: list[str] = []
    for bruta in t.split("\n"):
        linha = " ".join(_sem_simbolos(bruta).split()).strip(_PONTAS)
        if not linha or validador.contato_fora_da_loja(linha):
            continue
        frases = [f for f in _FIM_DE_FRASE.split(linha) if f.strip(_PONTAS)]
        boas = [f for f in frases if _frase_serve(f)]
        if boas:
            linhas.append(" ".join(boas).strip(_PONTAS))
    if not linhas:
        return None
    texto = "\n".join(linhas)
    if validador.contato_fora_da_loja(texto):
        return None
    return _cortar_linhas(texto, teto) or None


def _nome_do_atributo(a: dict) -> str | None:
    for chave in ("display_attribute_name", "original_attribute_name", "attribute_name", "name"):
        nome = _texto(a.get(chave))
        if nome:
            return nome
    return None


def _valor_do_item(v: dict) -> str | None:
    """Um valor de atributo: Shopee (`original_value_name` + `value_unit`), ML/TikTok (`name`)."""
    for chave in ("display_value_name", "original_value_name", "value_name", "name", "value"):
        valor = _texto(v.get(chave))
        if valor:
            unidade = _texto(v.get("value_unit"))
            if unidade and not _plano(valor).endswith(_plano(unidade)):
                valor = f"{valor} {unidade}"
            return valor
    return None


def _valores_do_atributo(a: dict) -> list[str]:
    valores: list[str] = []
    for v in (
        _lista(a.get("attribute_value_list"))
        or _lista(a.get("values"))
        or _lista(a.get("value_list"))
    ):
        valor = _valor_do_item(v)
        if valor:
            valores.append(valor)
    if not valores:
        valor = _texto(a.get("value_name")) or _texto(a.get("value"))
        if valor:
            valores.append(valor)
    return list(dict.fromkeys(v for v in valores if _plano(v) not in _ATRIBUTO_SEM_VALOR))


def _atributos_brutos(base: dict) -> list[tuple[str, str]]:
    """(nome, valor) de todos os atributos preenchidos que a IA pode usar, na ordem da loja.

    Shopee `attribute_list` (+ a marca de `brand`), ML `attributes`,
    TikTok `product_attributes` — o mesmo leitor para os três formatos.
    """
    pares: list[tuple[str, str]] = []
    marca = _texto(_dict(base.get("brand")).get("original_brand_name"))
    if marca and _plano(marca) not in _ATRIBUTO_SEM_VALOR and _valor_serve(marca):
        pares.append(("Marca", marca))
    for chave in ("attribute_list", "attributes", "product_attributes"):
        for a in _lista(base.get(chave)):
            nome = _nome_do_atributo(a)
            if not nome:
                continue
            # O id do ML ("SELLER_SKU", "IP_RATING") com espaço no lugar do "_".
            ident = (_texto(a.get("id")) or "").lower().replace("_", " ")
            if _ATRIBUTO_FORA.search(f"{_plano(nome)} {ident}"):
                continue
            valores = [v for v in _valores_do_atributo(a) if _valor_serve(v)]
            if valores:
                pares.append((nome, ", ".join(valores)))
    vistos: set[str] = set()
    saida: list[tuple[str, str]] = []
    for nome, valor in pares:
        chave = _plano(nome)
        if chave in vistos:
            continue
        vistos.add(chave)
        saida.append((nome, valor))
    return saida


# ── Ordem dos atributos: os da pergunta primeiro ──────────────────────────

_PALAVRA = re.compile(r"[a-z0-9]+")
_VAZIAS = frozenset(
    """
    a o e as os de da do das dos em no na nos nas um uma uns umas que com sem por para pra pro
    tem ter teria tinha esse essa este esta isso isto aquele aquela ele ela eles elas voce
    voces vc vcs qual quais quanto quantos quantas como onde quando sim nao ja mais menos
    muito pouco bom boa dia tarde noite ola oi obrigado obrigada favor gostaria queria quero
    saber pode podem possui possuem funcao vem vai ser sera era foi meu minha seu sua contem
    acompanha aqui ai la ok certo produto celular aparelho smartphone
    """.split()
)
# Sem palavra da pergunta, o que costuma ser perguntado vem antes.
_IMPORTANTES = (
    "marca",
    "brand",
    "modelo",
    "model",
    "memoria",
    "memory",
    "ram",
    "armazenamento",
    "storage",
    "rede",
    "network",
    "nfc",
    "tela",
    "screen",
    "display",
    "bateria",
    "battery",
    "camera",
    "processador",
    "processor",
    "sistema operacional",
    "operating",
    "cor",
    "color",
    "voltagem",
    "tensao",
    "voltage",
    "potencia",
    "power",
    "capacidade",
    "capacity",
    "material",
    "altura",
    "largura",
    "comprimento",
    "profundidade",
    "peso",
    "weight",
    "dimens",
    "tamanho",
    "size",
    "acessorio",
    "inclu",
    "compativ",
    "conector",
    "dual",
)


def palavras_da_pergunta(textos: Iterable[str | None]) -> frozenset[str]:
    """As palavras que contam do texto do cliente (sem acento, sem as vazias). PURA.

    Só ordenam os atributos — o texto do cliente nunca entra na ficha.
    """
    palavras: set[str] = set()
    for texto in textos:
        for p in _PALAVRA.findall(_plano(texto or "")):
            if p not in _VAZIAS and (len(p) >= 3 or any(ch.isdigit() for ch in p)):
                palavras.add(p)
    return frozenset(palavras)


def _bate(palavras: frozenset[str], plano: str) -> bool:
    """Alguma palavra da pergunta está na linha? (5 primeiras letras: plural, "memórias")"""
    for t in _PALAVRA.findall(plano):
        for p in palavras:
            if p == t or (len(p) >= 5 and len(t) >= 5 and p[:5] == t[:5]):
                return True
    return False


def _peso(nome: str, valor: str, palavras: frozenset[str]) -> int:
    plano = _plano(f"{nome} {valor}")
    if palavras and _bate(palavras, plano):
        return 3
    if any(x in _plano(nome) for x in _IMPORTANTES):
        return 2
    return 0 if _plano(valor) in _NAO else 1


def atributos(base: dict, palavras: frozenset[str] = frozenset()) -> list[str]:
    """Os atributos da ficha, "nome: valor", no máximo `MAX_ATRIBUTOS_FICHA`. PURA.

    Ordem: os que batem com a pergunta do cliente, os que costumam ser
    perguntados, o resto (o "Não" de recurso que ninguém perguntou por
    último) — dentro de cada grupo, a ordem da loja.
    """
    pares = _atributos_brutos(base)
    ordem = sorted(range(len(pares)), key=lambda i: (-_peso(pares[i][0], pares[i][1], palavras), i))
    saida: list[str] = []
    for i in ordem:
        nome, valor = pares[i]
        linha = _cortar(f"{nome}: {valor}", MAX_CHARS_ATRIBUTO)
        if validador.contato_fora_da_loja(linha):
            continue
        saida.append(linha)
        if len(saida) >= MAX_ATRIBUTOS_FICHA:
            break
    return saida


def _base(raw_data: Any) -> dict:
    """O dicionário do anúncio no `raw_data` (a Shopee antiga guarda em `item`)."""
    raw = _dict(raw_data)
    item = raw.get("item")
    return item if isinstance(item, dict) and item else raw


def _descricao_do_raw(base: dict) -> str | None:
    """A descrição dentro do `raw_data`: texto direto, ou a "estendida" da Shopee."""
    texto = base.get("description")
    if isinstance(texto, str) and texto.strip():
        return texto
    campos = _lista(
        _dict(_dict(base.get("description_info")).get("extended_description")).get("field_list")
    )
    partes = [_texto(c.get("text")) for c in campos if c.get("field_type") == "text"]
    return "\n".join(p for p in partes if p) or None


def montar_ficha(
    titulo: Any,
    descricao: Any,
    raw_data: Any,
    *,
    palavras: frozenset[str] = frozenset(),
) -> dict | None:
    """A ficha de um anúncio do catálogo — ou None sem atributo nem descrição. PURA.

    `{"titulo", "atributos": ["nome: valor", ...], "descricao"}` — só as
    chaves com conteúdo. Só título não é ficha (o título já vai nos FATOS).
    """
    base = _base(raw_data)
    lista = atributos(base, palavras)
    texto = limpar_descricao(
        descricao if isinstance(descricao, str) and descricao.strip() else _descricao_do_raw(base)
    )
    if not lista and not texto:
        return None
    ficha: dict[str, Any] = {}
    nome = _texto(titulo) or _texto(base.get("item_name")) or _texto(base.get("title"))
    if nome:
        ficha["titulo"] = _cortar(nome, MAX_CHARS_TITULO_FICHA)
    if lista:
        ficha["atributos"] = lista
    if texto:
        ficha["descricao"] = texto
    return ficha


def _tamanho(fichas: list[dict]) -> int:
    return len(json.dumps(fichas, ensure_ascii=False))


def _encolher(saida: list[dict], teto: int) -> list[dict]:
    """Corta `saida` (já copiada) até caber em `teto` — ver `caber`."""
    while saida and _tamanho(saida) > teto:
        ultima = saida[-1]
        excesso = _tamanho(saida) - teto
        if ultima.get("descricao"):
            nova = _cortar_linhas(ultima["descricao"], len(ultima["descricao"]) - excesso)
            if nova:
                ultima["descricao"] = nova
            else:
                ultima.pop("descricao")
        elif len(ultima.get("atributos") or []) > (MIN_ATRIBUTOS_NO_CORTE if len(saida) > 1 else 0):
            ultima["atributos"].pop()
            if not ultima["atributos"]:
                ultima.pop("atributos")
        else:
            saida.pop()
        if saida and not (saida[-1].get("descricao") or saida[-1].get("atributos")):
            saida.pop()
    return saida


def caber(fichas: list[dict], teto: int = MAX_CHARS_FICHAS) -> list[dict]:
    """As fichas em até `teto` caracteres (JSON). PURA (não muda as de entrada).

    O segundo anúncio em diante, cada um, primeiro em `MAX_CHARS_SEGUNDA_FICHA`.
    Depois o bloco: corta do ÚLTIMO anúncio (o menos recente) a descrição
    (linhas inteiras), depois os atributos até `MIN_ATRIBUTOS_NO_CORTE`,
    depois o anúncio inteiro. Sobrou um só e ainda não cabe: a descrição
    dele, depois os atributos (até nenhum). Ficha sem atributo nem descrição
    sai.
    """
    copias = [
        {**f, **({"atributos": list(f["atributos"])} if f.get("atributos") else {})} for f in fichas
    ]
    saida = copias[:1] + [g for f in copias[1:] for g in _encolher([f], MAX_CHARS_SEGUNDA_FICHA)]
    return _encolher(saida, teto)


# ── A ficha FRESCA (Redis): o que a loja devolveu ao enriquecimento ───────


def chave_ficha(plataforma: str, integration_id: Any, item_id: str) -> str:
    """A chave da ficha fresca — o mesmo recorte da do cartão (`enriquecer.chave_produto`)."""
    return f"atd:ficha:{plataforma}:{integration_id}:{item_id}"


def _bruta(titulo: Any, descricao: Any, raw: dict) -> dict | None:
    texto = descricao if isinstance(descricao, str) and descricao.strip() else None
    if not raw and texto is None:
        return None
    return {
        "titulo": _texto(titulo),
        "descricao": texto[:MAX_CHARS_DESCRICAO_BRUTA] if texto else None,
        "raw": raw,
    }


def ficha_bruta_shopee(item: Any) -> dict | None:
    """A ficha crua de um item do `get_item_base_info` da Shopee, enxuta. PURA.

    Título, a descrição (a normal, ou a "estendida" em texto) e os atributos
    preenchidos (`attribute_list`) e a marca, só com os campos que
    `montar_ficha` lê. None sem descrição nem atributo.
    """
    it = _dict(item)
    lista: list[dict] = []
    for a in _lista(it.get("attribute_list")):
        nome = {
            k: t
            for k in ("display_attribute_name", "original_attribute_name")
            if (t := _texto(a.get(k)))
        }
        valores = [
            v
            for bruto in _lista(a.get("attribute_value_list"))
            if (
                v := {
                    k: t
                    for k in ("display_value_name", "original_value_name", "value_unit")
                    if (t := _texto(bruto.get(k)))
                }
            )
        ]
        if nome and valores:
            lista.append({**nome, "attribute_value_list": valores})
    raw: dict[str, Any] = {}
    if lista:
        raw["attribute_list"] = lista
    marca = _texto(_dict(it.get("brand")).get("original_brand_name"))
    if marca:
        raw["brand"] = {"original_brand_name": marca}
    return _bruta(it.get("item_name"), _descricao_do_raw(it), raw)


def ficha_bruta_ml(body: Any, descricao: Any = None) -> dict | None:
    """A ficha crua de um anúncio do `/items` do ML (`attributes`), enxuta. PURA.

    `descricao` = o `/items/{id}/description` (a busca sob demanda,
    `ficha_loja.py`): o `/items` não traz o texto.
    """
    b = _dict(body)
    lista: list[dict] = []
    for a in _lista(b.get("attributes")):
        nome = _texto(a.get("name"))
        valores = [{"name": t} for v in _lista(a.get("values")) if (t := _texto(v.get("name")))]
        valor = _texto(a.get("value_name"))
        if nome and (valores or valor):
            lista.append(
                {"id": _texto(a.get("id")), "name": nome, "value_name": valor, "values": valores}
            )
    return _bruta(b.get("title"), descricao, {"attributes": lista} if lista else {})


def ficha_bruta_tiktok(data: Any) -> dict | None:
    """A ficha crua de um produto da TikTok (Get Product 202309, `data`), enxuta. PURA.

    Título, a descrição (HTML: a limpeza é a da `montar_ficha`), os
    atributos (`product_attributes`: `name` + `values[].name`) e a marca
    (`brand.name`). None sem descrição nem atributo.
    """
    d = _dict(data)
    lista: list[dict] = []
    for a in _lista(d.get("product_attributes")):
        nome = _texto(a.get("name"))
        valores = [{"name": t} for v in _lista(a.get("values")) if (t := _texto(v.get("name")))]
        if nome and valores:
            lista.append({"name": nome, "values": valores})
    raw: dict[str, Any] = {}
    if lista:
        raw["product_attributes"] = lista
    marca = _texto(_dict(d.get("brand")).get("name"))
    if marca:
        raw["brand"] = {"original_brand_name": marca}
    return _bruta(d.get("title"), d.get("description"), raw)


def _de_iso(bruto: Any) -> datetime | None:
    if not isinstance(bruto, str):
        return None
    try:
        quando = datetime.fromisoformat(bruto)
    except ValueError:
        return None
    return quando if quando.tzinfo else quando.replace(tzinfo=UTC)


def fonte_da_loja(
    plataforma: IntegrationPlatform, buscada_em: Any, agora: datetime | None = None
) -> str:
    """A `fonte` da ficha que a loja devolveu: "loja (Shopee), agora". PURA.

    A idade pela `buscada_em` (ISO), SEM número — os FATOS valem na
    conferência de número inventado. Sem a data (gravada antes dela): só a loja.
    """
    loja = _NOME_PLATAFORMA.get(plataforma, str(getattr(plataforma, "value", plataforma)))
    quando = _de_iso(buscada_em)
    if quando is None:
        return f"{FONTE_LOJA} ({loja})"
    idade = (agora or _agora()) - quando
    rotulo = (
        QUANDO_AGORA
        if idade < timedelta(hours=1)
        else QUANDO_HOJE
        if idade < timedelta(days=1)
        else QUANDO_DIAS
    )
    return f"{FONTE_LOJA} ({loja}), {rotulo}"


def _ficha_da_loja(
    bruta: dict, plataforma: IntegrationPlatform, titulo: str | None, palavras: frozenset[str]
) -> dict | None:
    """A ficha montada do que a loja devolveu (fresca ou buscada agora), com a fonte."""
    ficha = montar_ficha(
        bruta.get("titulo") or titulo, bruta.get("descricao"), bruta.get("raw"), palavras=palavras
    )
    if ficha is None:
        return None
    return {"fonte": fonte_da_loja(plataforma, bruta.get("buscada_em")), **ficha}


async def _ficha_fresca(
    plataforma: IntegrationPlatform, integration_id: UUID | None, item_id: str
) -> dict | None:
    """A ficha fresca do Redis, ou None (sem ela, sem Redis, ilegível)."""
    if integration_id is None:
        return None
    try:
        bruto = await redis.get(chave_ficha(plataforma.value, integration_id, item_id))
    except Exception as exc:  # noqa: BLE001 — sem Redis, segue para o catálogo
        logger.info("atendimento_ficha_cache_indisponivel", erro=type(exc).__name__)
        return None
    if not bruto:
        return None
    try:
        ficha = json.loads(bruto)
    except (TypeError, ValueError):
        return None
    return ficha if isinstance(ficha, dict) and isinstance(ficha.get("raw"), dict) else None


# ── Banco: o catálogo ─────────────────────────────────────────────────────


def _agora() -> datetime:
    return datetime.now(UTC)


def _com_conteudo():
    """A linha do catálogo tem descrição ou atributo (a da Amazon só tem título)."""
    return or_(
        func.coalesce(func.length(Listing.description), 0) > 0,
        Listing.raw_data.has_key("attribute_list"),
        Listing.raw_data.has_key("attributes"),
        Listing.raw_data.has_key("product_attributes"),
        Listing.raw_data["item"].has_key("attribute_list"),
    )


def _importado_ha_pouco():
    """A linha do catálogo foi (re)importada há até `MAX_DIAS_CATALOGO_FICHA` dias."""
    return Listing.imported_at >= _agora() - timedelta(days=MAX_DIAS_CATALOGO_FICHA)


async def _do_catalogo(
    session: AsyncSession,
    plataforma: IntegrationPlatform,
    item_id: str,
    integration_id: UUID | None,
) -> list[Any]:
    """As linhas do catálogo do PRÓPRIO anúncio: a da loja da conversa, a ativa, a mais nova."""
    ordem = [] if integration_id is None else [(Listing.integration_id == integration_id).desc()]
    return list(
        (
            await session.execute(
                select(Listing.title, Listing.description, Listing.raw_data, Listing.platform)
                .where(
                    Listing.platform == plataforma,
                    Listing.external_id == item_id,
                    _com_conteudo(),
                    _importado_ha_pouco(),
                )
                .order_by(
                    *ordem,
                    (Listing.status == ListingStatus.ACTIVE).desc(),
                    Listing.updated_at.desc(),
                )
                .limit(MAX_LINHAS_CATALOGO)
            )
        ).all()
    )


async def _do_mesmo_produto(
    session: AsyncSession, plataforma: IntegrationPlatform, item_id: str
) -> list[Any]:
    """Outros anúncios do MESMO produto (`product_links` → produtos → `product_links`).

    Só vínculo vivo (`morto_desde` vazio), na origem e no outro anúncio; e o
    outro anúncio só entra se TODOS os produtos vivos dele forem produtos da
    origem (o anúncio que junta outro modelo como variação fica de fora).
    Ordem: a mesma plataforma primeiro, o que tem mais produtos em comum (as
    variações), o que tem descrição, o ativo, o mais novo. Catálogo com o
    mesmo corte de data do próprio anúncio.
    """
    origem = select(ProductLink.product_id).where(
        ProductLink.platform == plataforma,
        ProductLink.external_id == item_id,
        ProductLink.morto_desde.is_(None),
    )
    para = aliased(ProductLink)
    fora = aliased(ProductLink)
    produto_de_fora = (
        exists()
        .where(
            fora.platform == para.platform,
            fora.external_id == para.external_id,
            fora.morto_desde.is_(None),
            fora.product_id.not_in(origem),
        )
        .correlate(para)
    )
    comuns = (
        select(
            para.platform.label("plataforma"),
            para.external_id.label("externo"),
            func.count(func.distinct(para.product_id)).label("comuns"),
        )
        .where(
            para.product_id.in_(origem),
            para.morto_desde.is_(None),
            or_(para.platform != plataforma, para.external_id != item_id),
            ~produto_de_fora,
        )
        .group_by(para.platform, para.external_id)
        .subquery()
    )
    tem_descricao = func.coalesce(func.length(Listing.description), 0) > 0
    return list(
        (
            await session.execute(
                select(Listing.title, Listing.description, Listing.raw_data, Listing.platform)
                .join(
                    comuns,
                    and_(
                        Listing.platform == comuns.c.plataforma,
                        Listing.external_id == comuns.c.externo,
                    ),
                )
                .where(_com_conteudo(), _importado_ha_pouco())
                .order_by(
                    (Listing.platform == plataforma).desc(),
                    comuns.c.comuns.desc(),
                    tem_descricao.desc(),
                    (Listing.status == ListingStatus.ACTIVE).desc(),
                    Listing.updated_at.desc(),
                )
                .limit(MAX_LINHAS_CATALOGO)
            )
        ).all()
    )


async def ficha_do_anuncio(
    session: AsyncSession,
    plataforma: IntegrationPlatform,
    item_id: str,
    integration_id: UUID | None = None,
    *,
    titulo: str | None = None,
    palavras: frozenset[str] = frozenset(),
    loja: Busca | None = None,
) -> dict | None:
    """A ficha de UM anúncio: a fresca; o catálogo; a loja; o mesmo produto em outro anúncio.

    `titulo` = o título ATUAL do anúncio, quando a conversa o tem: a linha do
    catálogo com outro título é de antes da mudança e não vale; a ficha sai
    com ele (o modelo vê de que anúncio é). `loja` (a sugestão,
    `fichas_da_conversa`): pode ir à loja buscar o anúncio (`ficha_loja.py`);
    None = não vai.
    """
    # Import tardio: `ficha_loja` importa este módulo.
    from app.services.atendimento import ficha_loja

    fresca = await _ficha_fresca(plataforma, integration_id, item_id)
    if fresca is not None and loja is not None and ficha_loja.falta_descricao(plataforma, fresca):
        # A fresca do ML (enriquecimento) não tem a descrição: só ela, da loja.
        completa = await ficha_loja.ficha_da_loja(session, loja, plataforma, item_id, fresca=fresca)
        fresca = completa or fresca
    if fresca is not None:
        ficha = _ficha_da_loja(fresca, plataforma, titulo, palavras)
        if ficha is not None:
            return ficha
    for nome, descricao, raw, _plat in await _do_catalogo(
        session, plataforma, item_id, integration_id
    ):
        if titulo and not mesmo_titulo(nome, titulo):
            continue
        ficha = montar_ficha(titulo or nome, descricao, raw, palavras=palavras)
        if ficha is not None:
            return ficha
    if fresca is None and loja is not None:
        # Sem fresca nem catálogo válido: o anúncio direto da loja da conversa.
        bruta = await ficha_loja.ficha_da_loja(session, loja, plataforma, item_id)
        ficha = _ficha_da_loja(bruta, plataforma, titulo, palavras) if bruta else None
        if ficha is not None:
            return ficha
    for nome, descricao, raw, plat in await _do_mesmo_produto(session, plataforma, item_id):
        ficha = montar_ficha(nome, descricao, raw, palavras=palavras)
        if ficha is None:
            continue
        nome_loja = _NOME_PLATAFORMA.get(plat, str(getattr(plat, "value", plat)))
        saida: dict[str, Any] = {"fonte": f"{FONTE_OUTRO_ANUNCIO} ({nome_loja})"}
        if titulo:
            saida["titulo"] = _cortar(titulo, MAX_CHARS_TITULO_FICHA)
        if ficha.get("titulo"):
            saida["titulo_do_outro_anuncio"] = ficha.pop("titulo")
        return {**saida, **ficha}
    return None


async def fichas_da_conversa(
    session: AsyncSession,
    conversa: AtendimentoConversa,
    mensagens: Sequence[AtendimentoMensagem],
    textos_cliente: Iterable[str | None] = (),
) -> list[dict]:
    """O bloco "ficha_do_anuncio" dos FATOS: até 2 anúncios, já no teto. [] = sem ficha.

    Para no PRIMEIRO anúncio sem ficha (ver o topo). `textos_cliente` (a
    última rajada do cliente) só ordena os atributos. Sem ficha fresca nem
    catálogo válido, vai à loja da conversa (`ficha_loja.py`): no máximo
    `ficha_loja.MAX_BUSCAS_POR_SUGESTAO` idas.
    """
    from app.services.atendimento import ficha_loja

    plataforma = plataforma_do_catalogo(conversa.plataforma)
    if plataforma is None:
        return []
    palavras = palavras_da_pergunta(textos_cliente)
    loja = ficha_loja.Busca(conversa.integration_id, conversa.canal_id)
    fichas: list[dict] = []
    for anuncio in anuncios_da_conversa(conversa, mensagens):
        ficha = await ficha_do_anuncio(
            session,
            plataforma,
            anuncio.item_id,
            conversa.integration_id,
            titulo=anuncio.titulo,
            palavras=palavras,
            loja=loja,
        )
        if ficha is None:
            break
        fichas.append({"de_onde": anuncio.de_onde, **ficha})
        if len(fichas) >= MAX_ANUNCIOS_FICHA:
            break
    return caber(fichas)
