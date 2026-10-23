"""O CATÁLOGO DA PRÓPRIA LOJA para o agente (item 6 da rodada "redondo", 23/10/2026).

Dono (23/10/2026): "faça ele realmente inteligente, pensando, pesquisando dentro do
DaVinci". Os casos reais que iam para pessoa por falta do dado:
  - 22/10 14:28–16:27, Barbosa Shopee (44ca7949, be5bfabc): "eu queria saber o valor
    somente [do] recipiente de vidro" — a loja tem o anúncio "Uranyx Cesto De Vidro Para
    Air Fryers" (22995184839), e o agente foi três vezes para pessoa com
    `dado_indisponivel`;
  - 21–22/10, Kia Shopee (fac02bf4, 0ca5b37b): "vcs tem a capa pro Uranyx A 18 Pró Max?"
    — a loja não vende capinha (o fato aprovado 26) e o agente mandava "buscar na
    Shopee";
  - "tem o A18?" vindo do anúncio do A17 (o A18 é outro anúncio da mesma loja);
  - o modelo que a loja não tem com esse nome (Doogee Fire 7 Ultra: a Kia e a Vita têm
    o Doogee Fire 6 Power e o Spunk Pro) — "não temos" nunca para modelo ou versão.

A busca (`buscar`) é SÓ LEITURA, sobre `product_links` da MESMA integração e da MESMA
plataforma da conversa (vínculo vivo: `morto_desde` vazio; nunca o Bling; nunca outra
loja), casando o título público do anúncio e o nome da variação (`listing_title` =
"título - variação"). Devolve até `MAX_ANUNCIOS` anúncios:
  {item_id, nome_curto (marca e modelo do título, sem garantia, condição nem
   especificação), variacoes_casadas, disponivel, da_conversa}
— sem preço, quantidade, SKU nem link. `disponivel` é calculado SÓ nas variações
casadas (sem variação no termo, em todas): algum vínculo com estoque > 0 e a última
sincronização ok → true; todos com estoque 0 → false; o resto → null (não se sabe).

As GUARDAS:
  - palavra de ACESSÓRIO no termo (capa, capinha, película, carregador, cesto, vidro,
    recipiente…) só casa anúncio cujo TÍTULO é desse acessório: a palavra está entre as
    3 primeiras do título e o título não é de celular (memória em GB, "12.128", RAM,
    smartphone) — os anúncios "Cappa/Case …" que são celulares não contam, nem a air
    fryer "com tigela de vidro";
  - todo o termo tem de casar (as palavras de MARCA e as genéricas — celular, aparelho,
    produto — são opcionais): "Doogee Fire 7 Ultra" não casa o "Doogee Fire 6 Power".

O que o resultado prova é só que a loja VENDE o produto e se ele está disponível — a
conferência G1 (`checagens.conferir_outro_produto_da_loja`) exige o `anuncio:<id>` dele
para toda frase de que a loja tem/vende outro produto, e o `ver_anuncio` de um anúncio
que não é da conversa nunca empresta característica ao produto da conversa.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import select

from app.models import ProductLink
from app.models.enums import LinkSyncStatus
from app.services.atendimento.validador import plano_de

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

MAX_ANUNCIOS = 5
MAX_VARIACOES_CASADAS = 6
MAX_CHARS_NOME_VARIACAO = 60
MAX_CHARS_TERMO = 80
MAX_CHARS_NOME_CURTO = 60
MAX_PALAVRAS_NOME_CURTO = 8
# Os vínculos lidos por busca (a Kia tem ~280 vivos na Shopee).
MAX_VINCULOS = 2_000

# ── As palavras ───────────────────────────────────────────────────────────

# Os acessórios (a forma canônica de cada grupo de sinônimos): a guarda do título e a
# conferência G1. "vidro" é material, mas o dono o pôs na lista (o "vidro da air fryer").
_ACESSORIOS: dict[str, str] = {
    "capa": "capa",
    "capinha": "capa",
    "case": "capa",
    "pelicula": "pelicula",
    "carregador": "carregador",
    "fonte": "carregador",
    "cabo": "cabo",
    "cesto": "cesto",
    "cesta": "cesto",
    "recipiente": "cesto",
    "tigela": "cesto",
    "pote": "cesto",
    "cuba": "cesto",
    "vidro": "vidro",
    "pulseira": "pulseira",
    "fone": "fone",
    "suporte": "suporte",
    "bateria": "bateria",
    "peca": "peca",
    "acessorio": "acessorio",
}
ACESSORIOS = frozenset(_ACESSORIOS.values())

# As marcas (opcionais no termo: "capa A18" acha a capa sem "Uranyx" no título).
MARCAS = frozenset(
    {
        "uranyx",
        "apple",
        "xiaomi",
        "redmi",
        "poco",
        "doogee",
        "oukitel",
        "fossibot",
        "blackview",
        "hotwav",
        "itel",
        "tecno",
        "motorola",
        "samsung",
        "oscal",
        "charlots",
    }
)
# As palavras genéricas do termo (opcionais): o cliente diz "o celular A18".
_GENERICAS = frozenset(
    {
        "celular",
        "smartphone",
        "aparelho",
        "telefone",
        "produto",
        "modelo",
        "versao",
        "item",
        "original",
        "novo",
        "nova",
        "avulso",
        "avulsa",
        "separado",
        "separada",
        "reposicao",
        "outro",
        "outra",
        "loja",
    }
)
_VAZIAS = frozenset(
    {
        "de",
        "da",
        "do",
        "das",
        "dos",
        "para",
        "pra",
        "o",
        "a",
        "os",
        "as",
        "um",
        "uma",
        "com",
        "e",
        "ou",
        "em",
        "no",
        "na",
        "nos",
        "nas",
        "so",
        "somente",
        "apenas",
        "tem",
        "voces",
        "vcs",
        "vende",
        "vendem",
        "valor",
        "preco",
        "quero",
        "comprar",
        "sua",
        "seu",
    }
)

_PALAVRA = re.compile(r"[a-z0-9]+")
# "A 18" → "a18"; "air fryer(s)" → "airfryer"; "4L"/"4 lts" → "4 litros"; "128gb" → "128 gb".
# Revisão do fechamento (23/10/2026): nunca a conjunção ou o artigo masculino ("8 GB físicos
# e 8 GB virtuais" não é o modelo "E8"); o "a" fica (o "A 18" do cliente é o A18).
_LETRA_E_NUMERO = re.compile(r"\b(?![eo]\b)([a-z])\s+(\d{1,3})\b")
_AIR_FRYER = re.compile(r"\bair\s*fryers?\b")
_LITROS = re.compile(r"\b(\d+(?:[.,]\d+)?)\s*(?:l|lt|lts|litros?)\b")
_NUMERO_E_UNIDADE = re.compile(r"\b(\d+)(gb|tb|mb|w|v|mah|ml|mm)\b")
# O título de CELULAR (a guarda do acessório): memória, RAM, "12.128".
_TITULO_DE_CELULAR = re.compile(
    r"\b\d+\s*(?:gb|tb)\b|\b\d{1,2}\.\d{2,3}\b|\bram\b|\bmemoria\b|\bsmartphones?\b"
    r"|\bcelular\w*\b"
)


def _singular(palavra: str) -> str:
    """O singular simples (simétrico: vale igual no termo e no título). PURA."""
    if len(palavra) <= 3 or not palavra.isalpha():
        return palavra
    if palavra.endswith("es") and palavra[-3] in "rzl":
        return palavra[:-2]
    if palavra.endswith("s") and not palavra.endswith("ss"):
        return palavra[:-1]
    return palavra


def plano(texto: Any) -> str:
    """O texto na forma de busca: sem acento, minúsculo, "A 18" → "a18", "air fryer" →
    "airfryer". PURA."""
    t = " ".join(plano_de(str(texto or "")).split())
    t = _AIR_FRYER.sub("airfryer", t)
    t = _LITROS.sub(r"\1 litros", t)
    t = _NUMERO_E_UNIDADE.sub(r"\1 \2", t)
    return _LETRA_E_NUMERO.sub(r"\1\2", t)


def canonica(palavra: str) -> str:
    """A palavra canônica (singular; o acessório pelo grupo). PURA."""
    p = _singular(palavra)
    return _ACESSORIOS.get(p, p)


def palavras(texto: Any) -> list[str]:
    """As palavras canônicas do texto, na ordem. PURA."""
    return [canonica(p) for p in _PALAVRA.findall(plano(texto))]


def acessorios_em(texto: Any) -> frozenset[str]:
    """Os acessórios (canônicos) que o texto cita. PURA."""
    return frozenset(p for p in palavras(texto) if p in ACESSORIOS)


@dataclass(frozen=True)
class Termo:
    """O termo da busca já lido: `obrigatorias` casam todas; `opcionais` (marca e
    genéricas) não precisam; `acessorios` ligam a guarda do título."""

    texto: str
    obrigatorias: tuple[str, ...]
    opcionais: tuple[str, ...]
    acessorios: frozenset[str]

    @property
    def modelo(self) -> bool:
        """O termo nomeia modelo ou versão (palavra com número e não é acessório)?"""
        return any(any(c.isdigit() for c in p) for p in self.obrigatorias)

    @property
    def vazio(self) -> bool:
        return not self.obrigatorias and not self.opcionais


def ler_termo(termo: Any) -> Termo:
    """O `Termo` de um texto livre do modelo (cortado em `MAX_CHARS_TERMO`). PURA."""
    texto = " ".join(str(termo or "").split())[:MAX_CHARS_TERMO]
    obrigatorias: list[str] = []
    opcionais: list[str] = []
    for p in palavras(texto):
        if p in _VAZIAS:
            continue
        destino = opcionais if p in MARCAS or p in _GENERICAS else obrigatorias
        if p not in destino:
            destino.append(p)
    return Termo(
        texto=texto,
        obrigatorias=tuple(obrigatorias),
        opcionais=tuple(opcionais),
        acessorios=frozenset(p for p in obrigatorias if p in ACESSORIOS),
    )


# ── O anúncio ─────────────────────────────────────────────────────────────

_SEPARADOR = " - "
# Revisão do fechamento (23/10/2026): o segmento que parece o valor de um NÍVEL de variação
# ("A17", "C17", "C59 Pro" — o modelo do anúncio de vários modelos), não um pedaço do título
# ("12.64 Memoria NFC", "12.128 GB").
_MAX_PALAVRAS_NIVEL = 3
_MAX_CHARS_NIVEL = 24
_NAO_E_NIVEL = re.compile(r"\d+[.,]\d+|\b(?:memoria|ram)\b")


def _parece_nivel(segmento: str) -> bool:
    plano_seg = plano_de(segmento).strip()
    return (
        bool(plano_seg)
        and len(plano_seg.split()) <= _MAX_PALAVRAS_NIVEL
        and len(plano_seg) <= _MAX_CHARS_NIVEL
        and not _NAO_E_NIVEL.search(plano_seg)
    )


def titulo_e_variacao_do_vinculo(titulo: str) -> tuple[str, str]:
    """(o título do anúncio quando o vínculo foi lido, o nome da variação) de UM
    `listing_title` ("título - variação"). PURA.

    Revisão do fechamento da rodada "redondo" (23/10/2026): a variação é o que vem depois
    do ÚLTIMO " - " (e o nível de antes, quando parece um: "A18 - Branco NFC", "C17 -
    Laranja") — nunca o pedaço do título velho que o vínculo guardou ("64 ou 128 Gb
    Memoria 12 Gb Ram - A17 - Preto,12 GB / 128 GB": a variação é "A17 - Preto,12 GB / 128
    GB", e o "64" do título velho não casa a variação de 128 GB)."""
    limpo = " ".join(str(titulo or "").split())
    segmentos = limpo.split(_SEPARADOR)
    if len(segmentos) == 1:
        return limpo.strip(" -"), ""
    n = 2 if len(segmentos) >= 3 and _parece_nivel(segmentos[-2]) else 1
    return (
        _SEPARADOR.join(segmentos[:-n]).strip(" -"),
        _SEPARADOR.join(segmentos[-n:]).strip(" -"),
    )


def _ate_a_palavra(comum: str, textos: Sequence[str]) -> str:
    """O começo comum sem a palavra cortada no meio ("Uranyx A18 Pro Max 1" → "Uranyx
    A18 Pro Max"). PURA."""
    if all(len(t) == len(comum) or t[len(comum)] in " -" for t in textos):
        return comum.strip(" -")
    corte = comum.rfind(" ")
    return comum[:corte].strip(" -") if corte > 0 else ""


def partes_do_anuncio(titulos: Sequence[str]) -> tuple[str, list[tuple[str, str]]]:
    """(título do anúncio, [(título do vínculo, variação)…]) a partir dos `listing_title`
    dos vínculos, na ordem deles (os vazios ficam de fora). PURA.

    O título do anúncio é o começo comum dos títulos dos vínculos, até a palavra inteira;
    quando eles divergem logo no começo (o anúncio renomeado: "Uranyx A17 Pro Max…" e
    "Uranyx Hotwav A17 Pro Max…"), o título mais comum."""
    limpos = [" ".join(str(t or "").split()) for t in titulos if str(t or "").strip()]
    if not limpos:
        return "", []
    partes = [titulo_e_variacao_do_vinculo(t) for t in limpos]
    proprios = [p for p, _v in partes]
    comum = proprios[0]
    for t in proprios[1:]:
        n = 0
        while n < min(len(comum), len(t)) and comum[n] == t[n]:
            n += 1
        comum = comum[:n]
    titulo = _ate_a_palavra(comum, proprios)
    if len(palavras(titulo)) < 2:
        titulo = max(sorted(set(proprios)), key=proprios.count)
    return titulo, partes


def titulo_e_variacoes(titulos: Sequence[str]) -> tuple[str, list[str]]:
    """(título do anúncio, nome de cada variação) a partir dos `listing_title` dos
    vínculos ("título - variação"), na ordem deles (`partes_do_anuncio`). PURA."""
    titulo, partes = partes_do_anuncio(titulos)
    return titulo, [v for _p, v in partes]


def titulo_do_acessorio(titulo: str, acessorios: Iterable[str]) -> bool:
    """O TÍTULO é de um destes acessórios? Um deles entre as 3 primeiras palavras
    (sem as vazias) e o título não é de celular. PURA."""
    plano_titulo = plano(titulo)
    if _TITULO_DE_CELULAR.search(plano_titulo):
        return False
    primeiras = [p for p in palavras(titulo) if p not in _VAZIAS][:3]
    return bool(set(primeiras) & set(acessorios))


# Onde o nome curto acaba: a especificação, a condição, a garantia e os conectivos.
_FIM_DO_NOME = frozenset(
    {
        "-",
        "+",
        "/",
        "|",
        ",",
        "com",
        "ou",
        "e",
        "a",
        "original",
        "lacrado",
        "novo",
        "nova",
        "usado",
        "usados",
        "recondicionado",
        "garantia",
        "resistente",
        "nfc",
        "5g",
        "4g",
        "memoria",
        "ram",
        "gb",
        "tela",
        "bivolt",
        "sem",
    }
)
_UNIDADE = re.compile(
    r"^(?:gb|tb|mb|g|w|v|mah|l|ml|mm|cm|kg|hz|mp|litros?|polegadas?|pecas?|unidades?)$"
)
_ESPEC = re.compile(r"^\d+(?:[.,/]\d+)+[a-z]*$|^\d+(?:gb|tb|mb|g|w|v|mah|l|ml|mm|cm|kg|hz|mp)$")


def nome_curto(titulo: str) -> str:
    """Marca e modelo do título, sem garantia, condição nem especificação. PURA.

    "Uranyx A18 Pro Max NFC 16.128 Gb Memoria S5 Premium - A18" → "Uranyx A18 Pro Max";
    "Doogee Fire 6 Power 20.256 GB - - Resistente…" → "Doogee Fire 6 Power";
    "AirFryer Fritadeira Digital 4 Litros Tigela de Vidro…" → "AirFryer Fritadeira Digital".
    """
    tokens = str(titulo or "").split()
    saida: list[str] = []
    for i, bruto in enumerate(tokens):
        p = plano_de(bruto).strip(".,;:!()[]*")
        seguinte = plano_de(tokens[i + 1]).strip(".,;:!()[]*") if i + 1 < len(tokens) else ""
        if not p or p in _FIM_DO_NOME or _ESPEC.match(p):
            break
        if p.isdigit() and _UNIDADE.match(seguinte):
            break
        saida.append(bruto.strip("*,;"))
        if bruto.endswith((",", ";")) or len(saida) >= MAX_PALAVRAS_NOME_CURTO:
            break
    nome = " ".join(s for s in saida if s)
    return nome[:MAX_CHARS_NOME_CURTO].strip() or " ".join(tokens[:3])[:MAX_CHARS_NOME_CURTO]


@dataclass(frozen=True)
class Vinculo:
    """Um vínculo vivo do anúncio (o que a busca lê de `product_links`)."""

    item_id: str
    variation_id: str | None
    titulo: str
    estoque: int | None
    sincronizado: bool


def disponivel(vinculos: Sequence[Vinculo]) -> bool | None:
    """Algum com estoque > 0 e a sincronização ok → True; todos com estoque 0 → False;
    o resto → None. PURA."""
    if not vinculos:
        return None
    if any((v.estoque or 0) > 0 and v.sincronizado for v in vinculos):
        return True
    if all(v.estoque == 0 for v in vinculos):
        return False
    return None


@dataclass(frozen=True)
class Achado:
    """Um anúncio que casou com o termo. `titulo_plano` (o título e as variações
    casadas, na forma de busca) fica só no `ctx` — a conferência G1 lê; o modelo não."""

    item_id: str
    nome_curto: str
    variacoes_casadas: tuple[str, ...]
    disponivel: bool | None
    da_conversa: bool
    titulo_plano: str
    ordem: int = 0

    def para_o_modelo(self) -> dict:
        return {
            "item_id": self.item_id,
            "nome_curto": self.nome_curto,
            "variacoes_casadas": list(self.variacoes_casadas),
            "disponivel": self.disponivel,
            "da_conversa": self.da_conversa,
        }


def casar(
    termo: Termo,
    vinculos: Sequence[Vinculo],
    *,
    da_conversa: bool = False,
) -> Achado | None:
    """O anúncio (os vínculos de UM item) casa com o termo? PURA.

    Todas as palavras obrigatórias no título do anúncio ou num vínculo; sem obrigatória,
    ao menos uma opcional no título. Com acessório no termo, o título tem de ser do
    acessório. Revisão do fechamento (23/10/2026): a palavra que falta no título escolhe
    os vínculos pela VARIAÇÃO deles (`titulo_e_variacao_do_vinculo`); só a que nenhuma
    variação tem escolhe pelo título do próprio vínculo ("S5", o "Hotwav" do título
    renomeado) — e o `disponivel` é o dos escolhidos.
    """
    # O vínculo sem título não casa nada (e desalinharia as variações dos outros).
    vinculos = [v for v in vinculos if str(v.titulo or "").strip()]
    if not vinculos or termo.vazio:
        return None
    titulo, partes = partes_do_anuncio([v.titulo for v in vinculos])
    if not titulo:
        return None
    do_titulo = set(palavras(titulo))
    if termo.acessorios and not titulo_do_acessorio(titulo, termo.acessorios):
        return None
    if not termo.obrigatorias:
        if not do_titulo & set(termo.opcionais):
            return None
        faltam: list[str] = []
    else:
        faltam = [p for p in termo.obrigatorias if p not in do_titulo]
    das_variacoes = [set(palavras(v)) for _p, v in partes]
    dos_proprios = [set(palavras(p)) for p, _v in partes]
    escolhidos_i = set(range(len(vinculos)))
    pela_variacao = False
    for p in faltam:
        na_variacao = {i for i, ws in enumerate(das_variacoes) if p in ws}
        if na_variacao:
            pela_variacao = True
            escolhidos_i &= na_variacao
        else:
            escolhidos_i &= {i for i, ws in enumerate(dos_proprios) if p in ws}
        if not escolhidos_i:
            return None
    ordem_i = sorted(escolhidos_i)
    escolhidos = [vinculos[i] for i in ordem_i]
    variacoes = (
        tuple(
            dict.fromkeys(
                partes[i][1][:MAX_CHARS_NOME_VARIACAO].strip() for i in ordem_i if partes[i][1]
            )
        )[:MAX_VARIACOES_CASADAS]
        if pela_variacao
        else ()
    )
    primeiras = [p for p in palavras(titulo) if p not in _VAZIAS][:5]
    ordem = -sum(1 for p in termo.obrigatorias if p in primeiras)
    proprios = list(dict.fromkeys(partes[i][0] for i in ordem_i if partes[i][0] != titulo))
    return Achado(
        item_id=vinculos[0].item_id,
        nome_curto=nome_curto(titulo),
        variacoes_casadas=variacoes,
        disponivel=disponivel(escolhidos),
        da_conversa=da_conversa,
        titulo_plano=plano(" ".join([titulo, *proprios, *variacoes])),
        ordem=ordem,
    )


def ordenar(achados: Iterable[Achado]) -> list[Achado]:
    """O mais aderente primeiro (o termo no começo do título), depois o disponível. PURA."""
    peso = {True: 0, None: 1, False: 2}
    return sorted(
        achados, key=lambda a: (a.ordem, peso[a.disponivel], len(a.nome_curto), a.item_id)
    )


async def vinculos_da_loja(
    s: AsyncSession, plataforma: Any, integration_id: UUID | None
) -> dict[str, list[Vinculo]]:
    """Os vínculos VIVOS da integração e da plataforma, por anúncio. Só leitura."""
    if integration_id is None or plataforma is None:
        return {}
    linhas = (
        await s.execute(
            select(
                ProductLink.external_id,
                ProductLink.variation_id,
                ProductLink.listing_title,
                ProductLink.stock,
                ProductLink.last_sync_status,
            )
            .where(
                ProductLink.integration_id == integration_id,
                ProductLink.platform == plataforma,
                ProductLink.morto_desde.is_(None),
                ProductLink.listing_title.is_not(None),
            )
            .order_by(ProductLink.external_id, ProductLink.variation_id)
            .limit(MAX_VINCULOS)
        )
    ).all()
    saida: dict[str, list[Vinculo]] = {}
    for linha in linhas:
        item = str(linha.external_id or "").strip()
        if not item:
            continue
        saida.setdefault(item, []).append(
            Vinculo(
                item_id=item,
                variation_id=str(linha.variation_id) if linha.variation_id else None,
                titulo=str(linha.listing_title),
                estoque=linha.stock,
                sincronizado=linha.last_sync_status == LinkSyncStatus.OK,
            )
        )
    return saida


# Os acessórios GENÉRICOS ("peça", "acessório"): a busca vazia nunca prova que a loja não
# vende — o "Uranyx Cesto De Vidro Para Air Fryers" é peça e não diz "peça" no título.
ACESSORIOS_GENERICOS = frozenset({"peca", "acessorio"})


def sem_no_titulo(
    acessorios: Iterable[str], por_anuncio: Mapping[str, Sequence[Vinculo]]
) -> list[str]:
    """Os acessórios específicos que NENHUM vínculo vivo da loja tem no título — em
    qualquer posição e sem a guarda do celular ("Case …" que é celular conta). PURA.

    Revisão do fechamento da rodada "redondo" (23/10/2026): a busca vazia do termo inteiro
    é artefato ("fone de ouvido bluetooth sem fio" não casa o "Uranyx Fone De Ouvido UFB10
    Bluetooth"; "carregador iphone" não casa o "Carregador … Compativel Apple") — só a
    ausência da palavra do acessório em todos os títulos prova que a loja não o vende.
    Com o teto de vínculos lido (`MAX_VINCULOS`), nada se prova."""
    especificos = [a for a in sorted(set(acessorios)) if a not in ACESSORIOS_GENERICOS]
    total = sum(len(v) for v in por_anuncio.values())
    if not especificos or total >= MAX_VINCULOS:
        return []
    vistas: set[str] = set()
    for vinculos in por_anuncio.values():
        for v in vinculos:
            vistas.update(palavras(v.titulo))
    return [a for a in especificos if a not in vistas]


def buscar_nos_vinculos(
    termo: Termo,
    por_anuncio: Mapping[str, Sequence[Vinculo]],
    *,
    da_conversa: Iterable[str] = (),
) -> list[Achado]:
    """Os anúncios que casam, ordenados, até `MAX_ANUNCIOS`. PURA."""
    citados = {str(i) for i in da_conversa}
    achados = [
        a
        for item, vinculos in por_anuncio.items()
        if (a := casar(termo, list(vinculos), da_conversa=item in citados)) is not None
    ]
    return ordenar(achados)[:MAX_ANUNCIOS]
