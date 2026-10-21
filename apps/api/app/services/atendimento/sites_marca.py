"""OS SITES DA MARCA como 3ª fonte de produto do AGENTE (20/10/2026).

O dono aprovou os sites (uranyx.com.br, charlots.com.br) como fonte em
20/10/2026, e a ordem: o anúncio da conversa vale primeiro, depois o
cadastro do DaVinci, e o site SÓ para o que faltar ("o site às vezes não tem
exatamente o que o anúncio tem"). Os dois são PHP renderizado no servidor,
sem API de especificação: este módulo lê as páginas de PRODUTO e grava um
instantâneo em `atendimento_site_produtos` (cron de madrugada, registrado
pelo worker; e o script `scripts/atendimento_sites_marca.py`).

O QUE LÊ (só GET, sem cookie, sem formulário, User-Agent próprio, tempo
curto, no máximo ~1 pedido por segundo):
  - Uranyx: a lista pelo `/sitemap.xml` e pelo `/llms.txt` (os dois; 24
    produtos em 20/10/2026), páginas `/produto/<slug>`: `h1` e `p.lead`
    (resumo) no `.detail-copy`, as linhas `.spec-row span`
    ("Característica"), `.detail-medidas dl dt/dd`, o aviso de 5G
    (`p.aviso-legal`) e as opções (`[data-cor-nome]`). O site não publica
    prazo de garantia, chip/eSIM, bandas nem o conteúdo da caixa; a RAM
    grande costuma somar a virtual ("20 GB de RAM (8 GB + até 12 GB…)").
  - Charlots: a lista pela página `/produtos` (30 produtos; o sitemap está
    VELHO — tem página que não existe mais, ex. `mala-grande-28`), páginas
    `/produto/<slug>`: `h1`, `p.detail-summary` (resumo), a descrição (o
    `p` do `.detail-copy`), o `dl` de Tamanho e Material, `.detail-medidas`,
    as opções (`[data-cor-nome]`: P1–P8 e M1–M6 são FAMÍLIAS de cor e
    design, não tamanho) e a frase "Garantia de 90 dias contra defeitos de
    fabricação" (`garantia_texto`). TSA só no Premium (P5–P8): vale a
    página do produto, nunca a home (que fala de TSA em geral e de uma mala
    de 28" que não existe).
Nunca a home nem página de categoria para afirmar característica. Página que
não é de produto (a "Página não encontrada", redirecionamento) não grava.

GRAVA só quando o conteúdo muda (`hash`); igual, só o `lido_em` anda.
Página que sumiu da lista não é apagada: deixa de ser relida e, passados
`MAX_DIAS_SEM_LER` dias, o casamento não a usa mais.

O CASAMENTO anúncio → página (`casar_anuncio_com_site`), pelo título do
anúncio: Uranyx pelo MODELO ("F110 Pro 5G", "F109 S", "C68 Plus", "S5" =
"A18 Pro Max - S5 Premium Edition"…; o mais específico vence, empate = não
casa) e só com a marca no título ou nos SKUs; Charlots pelo TAMANHO em
polegadas + a linha (ABS/polipropileno/policarbonato) + o tipo (mala,
frasqueira, kit/conjunto, com mochila, executiva). Na dúvida, não casa.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.atendimento import AtendimentoSiteProduto
from app.services.atendimento.constantes import SITES
from app.services.atendimento.validador import plano_de

logger = structlog.get_logger()

URANYX = "uranyx"
CHARLOTS = "charlots"

USER_AGENT = "DaVinci-Atendimento/1.0 (leitura de produto)"
TEMPO_LEITURA_S = 10.0
TEMPO_CONEXAO_S = 5.0
# Entre um pedido e outro ao mesmo site (~1 por segundo).
PAUSA_S = 1.0
# Teto de páginas por site numa leitura (o catálogo tem 24 e 30).
MAX_PAGINAS_POR_SITE = 60
# Uma página de produto tem ~50 KB; acima disto não é página de produto.
MAX_BYTES_PAGINA = 1_500_000
MAX_CHARS_VALOR = 600
MAX_CHARS_DESCRICAO = 1_500
MAX_OPCOES = 150
# Página não relida há mais que isto não vale para o casamento.
MAX_DIAS_SEM_LER = 30

ROTULO_CARACTERISTICA = "Característica"
ROTULO_AVISO = "Aviso"
ROTULO_OPCOES = "Opções"
ROTULO_DESCRICAO = "Descrição"
ROTULO_CATEGORIA = "Categoria"
ROTULO_LINHA = "Linha"

_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,119}$")
_LINK_PRODUTO = re.compile(r"/produto/([a-z0-9][a-z0-9-]{0,119})(?=[\"'<>\s)#?]|$)")
_GARANTIA = re.compile(r"Garantia de \d+ dias[^.<\n]{0,80}", re.IGNORECASE)


# ── HTML → árvore (html.parser, sem dependência nova) ─────────────────────


@dataclass
class _No:
    tag: str
    attrs: dict[str, str]
    filhos: list[_No | str] = field(default_factory=list)

    @property
    def classes(self) -> set[str]:
        return set((self.attrs.get("class") or "").split())


_VAZIAS = frozenset("area base br col embed hr img input link meta param source track wbr".split())
_SEM_TEXTO = frozenset({"script", "style", "noscript", "template", "svg"})


class _Arvore(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.raiz = _No("#raiz", {})
        self._pilha = [self.raiz]
        self._ignorar = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._ignorar:
            if tag in _SEM_TEXTO:
                self._ignorar += 1
            return
        if tag in _SEM_TEXTO:
            self._ignorar = 1
            return
        no = _No(tag, {k: v or "" for k, v in attrs})
        self._pilha[-1].filhos.append(no)
        if tag not in _VAZIAS:
            self._pilha.append(no)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if not self._ignorar and tag not in _SEM_TEXTO:
            self._pilha[-1].filhos.append(_No(tag, {k: v or "" for k, v in attrs}))

    def handle_endtag(self, tag: str) -> None:
        if self._ignorar:
            if tag in _SEM_TEXTO:
                self._ignorar -= 1
            return
        for i in range(len(self._pilha) - 1, 0, -1):
            if self._pilha[i].tag == tag:
                del self._pilha[i:]
                return

    def handle_data(self, data: str) -> None:
        if not self._ignorar and data:
            self._pilha[-1].filhos.append(data)


def _arvore(html: str) -> _No:
    p = _Arvore()
    p.feed(html)
    p.close()
    return p.raiz


def _todos(no: _No, filtro) -> list[_No]:
    """Os descendentes que passam no filtro, na ordem do documento."""
    saida: list[_No] = []
    pilha: list[_No] = [no]
    while pilha:
        atual = pilha.pop()
        for filho in reversed(atual.filhos):
            if isinstance(filho, _No):
                pilha.append(filho)
        if atual is not no and filtro(atual):
            saida.append(atual)
    return saida


def _primeiro(no: _No | None, filtro) -> _No | None:
    if no is None:
        return None
    achados = _todos(no, filtro)
    return achados[0] if achados else None


def _com_classe(classe: str, tag: str | None = None):
    return lambda n: classe in n.classes and (tag is None or n.tag == tag)


def _tag(tag: str):
    return lambda n: n.tag == tag


def _texto_de(no: _No | None, quebra: bool = False) -> str:
    """O texto do nó, espaços normalizados; `quebra` = `<br>` vira linha."""
    if no is None:
        return ""
    partes: list[str] = []

    def _andar(n: _No) -> None:
        for f in n.filhos:
            if isinstance(f, str):
                partes.append(f)
            elif f.tag == "br":
                partes.append("\n" if quebra else " ")
            else:
                _andar(f)

    _andar(no)
    bruto = "".join(partes)
    if quebra:
        linhas = (" ".join(linha.split()) for linha in bruto.split("\n"))
        return "\n".join(linha for linha in linhas if linha)
    return " ".join(bruto.split())


def _cortar(texto: str, teto: int) -> str:
    return texto if len(texto) <= teto else texto[: teto - 1].rstrip(" ,;:-") + "…"


def _pares_dl(dl: _No) -> list[dict]:
    """Os pares dt/dd de um `dl` (direto ou dentro de `div`), na ordem."""
    saida: list[dict] = []
    rotulo: str | None = None
    for n in _todos(dl, lambda x: x.tag in ("dt", "dd")):
        if n.tag == "dt":
            rotulo = _texto_de(n) or None
        elif rotulo:
            if valor := _texto_de(n):
                saida.append({"rotulo": rotulo, "valor": _cortar(valor, MAX_CHARS_VALOR)})
            rotulo = None
    return saida


def _opcoes(raiz: _No) -> dict | None:
    nomes: list[str] = []
    for n in _todos(raiz, lambda x: "data-cor-nome" in x.attrs):
        nome = " ".join(n.attrs["data-cor-nome"].split())
        if nome and nome not in nomes:
            nomes.append(nome)
    if not nomes:
        return None
    return {"rotulo": ROTULO_OPCOES, "valor": _cortar(", ".join(nomes[:MAX_OPCOES]), 3_000)}


def _secao_do_produto(raiz: _No, copia: _No | None) -> _No | None:
    """A `section` da página que tem o `.detail-copy` (as opções do produto; os cartões
    de "Produtos similares" ficam em outra seção e também têm `data-cor-nome`)."""
    if copia is None:
        return None
    for secao in _todos(raiz, _tag("section")):
        if any(n is copia for n in _todos(secao, lambda _x: True)):
            return secao
    return None


def _canonica_bate(raiz: _No, slug: str) -> bool:
    """O `link rel=canonical`, quando há, é a página deste produto."""
    for n in _todos(raiz, lambda x: x.tag == "link" and x.attrs.get("rel") == "canonical"):
        caminho = urlsplit(n.attrs.get("href") or "").path.rstrip("/")
        return caminho == f"/produto/{slug}"
    return True


# ── A página de produto ───────────────────────────────────────────────────


@dataclass(frozen=True)
class PaginaProduto:
    marca: str
    slug: str
    url: str
    titulo: str
    modelo_chave: str | None
    specs: list[dict]
    resumo: str | None
    garantia_texto: str | None

    def hash(self) -> str:
        """O hash do CONTEÚDO (o que muda a gravação)."""
        dado = json.dumps(
            [
                self.url,
                self.titulo,
                self.modelo_chave,
                self.specs,
                self.resumo,
                self.garantia_texto,
            ],
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(dado.encode("utf-8")).hexdigest()


def _plano(texto: Any) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", plano_de(str(texto or ""))))


# O modelo de cada página da Uranyx que não é o próprio `h1`.
_CHAVE_POR_SLUG_URANYX = {"s5": "s5"}


def ler_pagina_uranyx(html: str, slug: str, url: str) -> PaginaProduto | None:
    """A página de produto da Uranyx — ou None (não é página de produto). PURA."""
    raiz = _arvore(html)
    copia = _primeiro(raiz, _com_classe("detail-copy"))
    h1 = _primeiro(copia, _tag("h1"))
    titulo = _texto_de(h1)
    if not titulo or not _canonica_bate(raiz, slug):
        return None
    specs: list[dict] = []
    if categoria := _texto_de(_primeiro(copia, _com_classe("eyebrow", "span"))):
        specs.append({"rotulo": ROTULO_CATEGORIA, "valor": categoria})
    for linha in _todos(raiz, _com_classe("spec-row")):
        if valor := _texto_de(_primeiro(linha, _tag("span"))):
            specs.append(
                {"rotulo": ROTULO_CARACTERISTICA, "valor": _cortar(valor, MAX_CHARS_VALOR)}
            )
    for medidas in _todos(raiz, _com_classe("detail-medidas")):
        for dl in _todos(medidas, _tag("dl")):
            specs.extend(_pares_dl(dl))
    for aviso in _todos(raiz, _com_classe("aviso-legal", "p")):
        if valor := _texto_de(aviso):
            specs.append({"rotulo": ROTULO_AVISO, "valor": _cortar(valor, MAX_CHARS_VALOR)})
    if opcoes := _opcoes(_secao_do_produto(raiz, copia) or raiz):
        specs.append(opcoes)
    if not any(s["rotulo"] not in (ROTULO_CATEGORIA, ROTULO_OPCOES) for s in specs):
        return None  # sem característica nem medida: não é página de produto
    resumo = _texto_de(_primeiro(copia, _com_classe("lead", "p"))) or None
    texto = _texto_de(copia)
    garantia = m.group(0).strip() if (m := _GARANTIA.search(texto)) else None
    chave = _CHAVE_POR_SLUG_URANYX.get(slug) or _plano(titulo)[:64] or None
    return PaginaProduto(URANYX, slug, url, titulo, chave, specs, resumo, garantia)


# Um número inteiro de 1–2 dígitos que não é decimal ("6,9") nem peso,
# medida, volume, potência ou voltagem ("10 kg", "23 cm", "4 L").
_NUMERO_SOLTO = re.compile(
    r"(?<!\d)(?<!\d[.,])(\d{1,2})(?!\d|[.,]\d|\s*(?:kg|g|cm|mm|l|w|v|gb|pecas|malas)\b)"
)
# As polegadas que existem em mala (o número solto do título do anúncio).
_POLEGADAS_DE_MALA = frozenset({8, 12, 13, 14, 16, 18, 20, 24, 26, 28, 30})
_PECAS = re.compile(r"\b(\d{1,2})\s*(?:pecas|malas|itens)\b")


def _tamanhos(texto: str) -> list[int]:
    """As polegadas de um texto ("14, 20 e 24 polegadas", "20\\"") — na ordem."""
    saida: list[int] = []
    plano = plano_de(texto)
    if re.search(r"polegada|\bpol\b|[\"”″]", plano):
        for n in _NUMERO_SOLTO.findall(plano):
            if 6 <= int(n) <= 32 and int(n) not in saida:
                saida.append(int(n))
    return saida


def _tamanhos_das_pecas(specs: Sequence[dict]) -> list[int]:
    """As polegadas das peças nas medidas do kit ('Mala de bordo 20"', 'Frasqueira 12"').

    A peça alternativa ("16\" … no lugar da 14\"") fica de fora.
    """
    saida: list[int] = []
    for s in specs:
        rotulo = str(s.get("rotulo") or "")
        if "no lugar" in plano_de(rotulo):
            continue
        for n in re.findall(r"(\d{1,2})\s*[\"”″]", rotulo):
            if int(n) not in saida:
                saida.append(int(n))
    return saida


def _pecas(texto: str) -> int | None:
    m = _PECAS.search(_plano(texto))
    return int(m.group(1)) if m else None


_MATERIAIS = {
    "polipropileno": "polipropileno",
    "pp": "polipropileno",
    "abs": "abs",
    "policarbonato": "policarbonato",
    "pc": "policarbonato",
}


def _material(texto: str) -> str | None:
    achados = {_MATERIAIS[p] for p in _plano(texto).split() if p in _MATERIAIS}
    return achados.pop() if len(achados) == 1 else None


def _tipo_charlots(texto: str) -> str:
    """O tipo pelo nome: kit (kit/conjunto/N peças), frasqueira, mochila, executiva, mala, outro."""
    p = _plano(texto)
    if re.search(r"\b(?:kit|conjunto|jogo)\b|\b\d\s*pecas\b", p):
        return "kit"
    if re.search(r"\bexecutiva\b", p):
        return "executiva"
    if re.search(r"\bfrasqueira\b|\bnecessaire\b", p) and not re.search(r"\bmalas\b", p):
        return "frasqueira"
    if re.search(r"\bmala\b|\bmalas\b", p):
        return "mala"
    if re.search(r"\bmochila\b", p):
        return "mochila"
    return "outro"


def _spec(specs: Sequence[dict], rotulo: str) -> str:
    return str(next((s.get("valor") for s in specs if _plano(s.get("rotulo")) == rotulo), "") or "")


def _chave_charlots(titulo: str, specs: Sequence[dict]) -> str | None:
    material = _spec(specs, "material")
    tamanhos = _tamanhos(_spec(specs, "tamanho")) or _tamanhos_das_pecas(specs) or _tamanhos(titulo)
    partes = [_tipo_charlots(titulo)]
    if "mochila" in _plano(titulo) and partes[0] != "mochila":
        partes.append("mochila")
    if m := _material(material) or _material(titulo):
        partes.append(m)
    if tamanhos:
        partes.append("-".join(str(t) for t in sorted(tamanhos)))
    return " ".join(partes)[:64] or None


def ler_pagina_charlots(html: str, slug: str, url: str) -> PaginaProduto | None:
    """A página de produto da Charlots — ou None (não é página de produto). PURA."""
    raiz = _arvore(html)
    copia = _primeiro(raiz, _com_classe("detail-copy"))
    h1 = _primeiro(copia, _tag("h1"))
    titulo = _texto_de(h1)
    if copia is None or not titulo or not _canonica_bate(raiz, slug):
        return None
    specs: list[dict] = []
    diretos = [f for f in copia.filhos if isinstance(f, _No)]
    if linha := next(
        (_texto_de(f) for f in diretos if f.tag == "span" and "eyebrow" in f.classes), ""
    ):
        specs.append({"rotulo": ROTULO_LINHA, "valor": linha})
    resumo = next(
        (_texto_de(f) for f in diretos if f.tag == "p" and "detail-summary" in f.classes), ""
    )
    descricao = next(
        (_texto_de(f, quebra=True) for f in diretos if f.tag == "p" and not f.classes), ""
    )
    if descricao:
        specs.append({"rotulo": ROTULO_DESCRICAO, "valor": _cortar(descricao, MAX_CHARS_DESCRICAO)})
    for f in diretos:
        if f.tag == "dl":
            specs.extend(_pares_dl(f))
    for medidas in _todos(copia, _com_classe("detail-medidas")):
        for dl in _todos(medidas, _tag("dl")):
            specs.extend(_pares_dl(dl))
    if opcoes := _opcoes(_secao_do_produto(raiz, copia) or raiz):
        specs.append(opcoes)
    if not any(_plano(s["rotulo"]) in ("tamanho", "dimensoes", "material") for s in specs):
        return None  # a "Página não encontrada" e as de categoria não têm
    garantia = m.group(0).strip() if (m := _GARANTIA.search(descricao or "")) else None
    return PaginaProduto(
        CHARLOTS,
        slug,
        url,
        titulo,
        _chave_charlots(titulo, specs),
        specs,
        resumo or None,
        garantia,
    )


_LEITORES = {URANYX: ler_pagina_uranyx, CHARLOTS: ler_pagina_charlots}


def ler_pagina(marca: str, html: str, slug: str, url: str) -> PaginaProduto | None:
    """A página de produto da marca — ou None. PURA."""
    leitor = _LEITORES.get(marca)
    return leitor(html, slug, url) if leitor else None


# ── As listas de produtos ─────────────────────────────────────────────────


def slugs_da_lista(*textos: str | None) -> list[str]:
    """Os slugs de produto (`/produto/<slug>`) dos textos (sitemap, llms.txt, /produtos). PURA."""
    saida: list[str] = []
    for texto in textos:
        for slug in _LINK_PRODUTO.findall(texto or ""):
            if _SLUG.match(slug) and slug not in saida:
                saida.append(slug)
    return saida[:MAX_PAGINAS_POR_SITE]


# A(s) página(s) de lista de cada site (a Charlots não usa o sitemap: velho).
LISTAS: dict[str, tuple[str, ...]] = {
    URANYX: ("/sitemap.xml", "/llms.txt"),
    CHARLOTS: ("/produtos",),
}


# ── HTTP ──────────────────────────────────────────────────────────────────


def _base(marca: str) -> str | None:
    # O mesmo endereço do carrinho dos sites (`atendimento_sites_urls` ou o de produção).
    from app.services.atendimento.carrinhos import url_do_site

    return url_do_site(marca)


def novo_cliente() -> httpx.AsyncClient:
    """O cliente HTTP da leitura: sem redirecionamento, tempo curto, User-Agent próprio."""
    return httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xml,text/plain"},
        timeout=httpx.Timeout(TEMPO_LEITURA_S, connect=TEMPO_CONEXAO_S),
        follow_redirects=False,
    )


class _Ritmo:
    """~1 pedido por segundo ao mesmo site."""

    def __init__(self, pausa_s: float) -> None:
        self.pausa_s = pausa_s
        self._ultimo: float | None = None

    async def esperar(self) -> None:
        agora = asyncio.get_running_loop().time()
        if self._ultimo is not None:
            falta = self.pausa_s - (agora - self._ultimo)
            if falta > 0:
                await asyncio.sleep(falta)
        self._ultimo = asyncio.get_running_loop().time()


async def _get(cliente: httpx.AsyncClient, url: str, ritmo: _Ritmo) -> str | None:
    """O corpo de um GET 200 de texto — ou None (erro, redirecionamento, grande demais)."""
    await ritmo.esperar()
    cliente.cookies.clear()  # nunca manda cookie de volta
    try:
        resposta = await cliente.get(url)
    except httpx.HTTPError as exc:
        logger.info("atendimento_sites_marca_get_falhou", erro=type(exc).__name__)
        return None
    if resposta.status_code != 200 or len(resposta.content) > MAX_BYTES_PAGINA:
        logger.info("atendimento_sites_marca_get_recusado", status=resposta.status_code)
        return None
    return resposta.text


# ── Gravação ──────────────────────────────────────────────────────────────


async def gravar_pagina(session: AsyncSession, pagina: PaginaProduto, agora: datetime) -> str:
    """Grava a página → "nova" | "mudou" | "igual" (igual: só o `lido_em`)."""
    hash_ = pagina.hash()
    atual = await session.scalar(
        select(AtendimentoSiteProduto).where(
            AtendimentoSiteProduto.marca == pagina.marca,
            AtendimentoSiteProduto.slug == pagina.slug,
        )
    )
    if atual is None:
        session.add(
            AtendimentoSiteProduto(
                marca=pagina.marca,
                slug=pagina.slug,
                url=pagina.url,
                titulo=pagina.titulo,
                modelo_chave=pagina.modelo_chave,
                specs=pagina.specs,
                resumo=pagina.resumo,
                garantia_texto=pagina.garantia_texto,
                lido_em=agora,
                hash=hash_,
            )
        )
        return "nova"
    atual.lido_em = agora
    if atual.hash == hash_:
        return "igual"
    atual.url = pagina.url
    atual.titulo = pagina.titulo
    atual.modelo_chave = pagina.modelo_chave
    atual.specs = pagina.specs
    atual.resumo = pagina.resumo
    atual.garantia_texto = pagina.garantia_texto
    atual.hash = hash_
    return "mudou"


async def atualizar_sites(
    session: AsyncSession,
    *,
    marcas: Iterable[str] = SITES,
    cliente: httpx.AsyncClient | None = None,
    gravar: bool = True,
    forcar: bool = False,
    pausa_s: float = PAUSA_S,
    agora: datetime | None = None,
) -> dict[str, Any]:
    """Lê as páginas de produto dos sites e grava o instantâneo (o cron de madrugada).

    `gravar=False` = só lê e conta (o `--dry-run` do script). `forcar` = lê
    mesmo com `atendimento_sites_marca_ativo` desligado (o script). Só
    `flush`: quem chama faz o commit (o `session_scope` do cron). Falha de
    um site ou de uma página não derruba o resto. O resumo só tem contagens.
    """
    if not forcar and not get_settings().atendimento_sites_marca_ativo:
        return {"desligado": True}
    agora = agora or datetime.now(UTC)
    proprio = cliente is None
    http = cliente or novo_cliente()
    resumo: dict[str, Any] = {}
    try:
        for marca in marcas:
            conta = {"lista": 0, "lidas": 0, "nao_produto": 0, "falhas": 0}
            conta.update({"nova": 0, "mudou": 0, "igual": 0})
            resumo[marca] = conta
            base = _base(marca)
            if base is None or marca not in _LEITORES:
                conta["falhas"] += 1
                continue
            ritmo = _Ritmo(pausa_s)
            listas = [await _get(http, base + caminho, ritmo) for caminho in LISTAS[marca]]
            slugs = slugs_da_lista(*listas)
            conta["lista"] = len(slugs)
            for slug in slugs:
                url = f"{base}/produto/{slug}"
                html = await _get(http, url, ritmo)
                if html is None:
                    conta["falhas"] += 1
                    continue
                pagina = ler_pagina(marca, html, slug, url)
                if pagina is None:
                    conta["nao_produto"] += 1
                    continue
                conta["lidas"] += 1
                if gravar:
                    conta[await gravar_pagina(session, pagina, agora)] += 1
            if gravar:
                await session.flush()
            logger.info("atendimento_sites_marca", marca=marca, **conta)
    finally:
        if proprio:
            await http.aclose()
    return resumo


# ── O casamento anúncio → página ──────────────────────────────────────────

# O recorte de SKU de cada marca (o de `routers/sites_estoque.ESCOPOS`, sem os
# acessórios `a0NN`, que são das duas).
_SKU_MARCA = {
    URANYX: re.compile(r"^(?:u|dg[0-9])", re.IGNORECASE),
    CHARLOTS: re.compile(r"^(?:b[0-9]|bp[0-9])", re.IGNORECASE),
}

# Os outros nomes de um modelo da Uranyx no título do anúncio.
_APELIDOS_URANYX: dict[str, tuple[tuple[str, ...], ...]] = {
    "s5": (("s5",), ("a18", "pro", "max")),
}


def marca_do_anuncio(titulo: str | None, skus: Iterable[str | None] = ()) -> str | None:
    """A marca do anúncio pelo título ("Uranyx", "Charlots") ou pelos SKUs — ou None. PURA."""
    palavras = set(_plano(titulo).split())
    pelo_titulo = {m for m in SITES if m in palavras}
    if len(pelo_titulo) == 1:
        return pelo_titulo.pop()
    pelos_skus = {m for sku in skus if sku for m, rx in _SKU_MARCA.items() if rx.match(sku.strip())}
    return pelos_skus.pop() if len(pelos_skus) == 1 and not pelo_titulo else None


def _contem(palavras: list[str], chave: Sequence[str]) -> bool:
    n = len(chave)
    return n > 0 and any(tuple(palavras[i : i + n]) == tuple(chave) for i in range(len(palavras)))


# O anúncio que COMEÇA por uma destas é acessório de outro produto ("Capa
# Uranyx S5", "Cesto de vidro Air Fryer"): não casa com a página do produto.
_ACESSORIO_NO_COMECO = frozenset(
    "capa capinha case pelicula cesto tigela forma suporte cabo carregador pulseira".split()
)


def _casar_uranyx(titulo: str, paginas: Sequence[Any]) -> Any | None:
    """O modelo mais específico contido no título (empate = não casa)."""
    palavras = _plano(titulo).split()
    comeco = set(palavras[:3]) & _ACESSORIO_NO_COMECO
    melhor: list[tuple[int, Any]] = []
    for p in paginas:
        chave = getattr(p, "modelo_chave", None) or ""
        if comeco - set(chave.split()):
            continue
        formas = _APELIDOS_URANYX.get(chave) or (tuple(chave.split()),)
        tamanho = max((len(f) for f in formas if _contem(palavras, f)), default=0)
        if tamanho:
            melhor.append((tamanho, p))
    if not melhor:
        return None
    topo = max(t for t, _ in melhor)
    vencedoras = [p for t, p in melhor if t == topo]
    return vencedoras[0] if len(vencedoras) == 1 else None


def _do_anuncio_charlots(titulo: str) -> tuple[str, bool, str | None, frozenset[int]]:
    plano = _plano(titulo)
    tipo = _tipo_charlots(titulo)
    mochila = "mochila" in plano.split() and tipo != "mochila"
    tamanhos = _tamanhos(titulo)
    if not tamanhos:
        # "Mala Bordo 20 ABS": o número solto entre os tamanhos de mala.
        tamanhos = [int(n) for n in _NUMERO_SOLTO.findall(plano) if int(n) in _POLEGADAS_DE_MALA]
    if len(set(tamanhos)) >= 2 and tipo in ("mala", "frasqueira"):
        tipo = "kit"  # "Mala e Frasqueira 14 e 20": duas peças
    return tipo, mochila, _material(titulo), frozenset(tamanhos)


_FAMILIA = re.compile(r"\b((?:me|m|p)\d)\b")


def _familias(texto: str) -> set[str]:
    """Os códigos de família (P1–P8, M1–M6, ME1–ME2: cor e design, não tamanho)."""
    return set(_FAMILIA.findall(_plano(texto)))


def _casar_charlots(titulo: str, paginas: Sequence[Any]) -> Any | None:
    """Tipo, mochila, linha e polegadas iguais (e o nº de peças, quando os dois dizem)."""
    tipo, mochila, material, tamanhos = _do_anuncio_charlots(titulo)
    if not tamanhos or tipo == "outro":
        return None
    pecas = _pecas(titulo)
    familias = _familias(titulo)
    candidatas = []
    for p in paginas:
        p_titulo = getattr(p, "titulo", "") or ""
        specs = [s for s in (getattr(p, "specs", None) or []) if isinstance(s, dict)]
        p_tipo = _tipo_charlots(p_titulo)
        p_mochila = "mochila" in _plano(p_titulo).split() and p_tipo != "mochila"
        # O kit: as polegadas do "Tamanho" ou as das peças nas medidas.
        conjuntos = {
            frozenset(t)
            for t in (_tamanhos(_spec(specs, "tamanho")), _tamanhos_das_pecas(specs))
            if t
        } or {frozenset(_tamanhos(p_titulo))}
        p_material = _material(_spec(specs, "material")) or _material(p_titulo)
        p_pecas = _pecas(p_titulo)
        p_familias = _familias(_spec(specs, "opcoes"))
        if (
            p_tipo == tipo
            and p_mochila == mochila
            and tamanhos in conjuntos
            and (material is None or p_material == material)
            and (pecas is None or p_pecas is None or pecas == p_pecas)
            and (not familias or not p_familias or familias <= p_familias)
        ):
            candidatas.append(p)
    return candidatas[0] if len(candidatas) == 1 else None


def casar(titulo: str | None, paginas: Sequence[Any], marca: str | None = None) -> Any | None:
    """A página do site que é o produto do anúncio — ou None (na dúvida, não casa). PURA.

    `paginas` = linhas de `atendimento_site_produtos` ou `PaginaProduto` (marca,
    titulo, modelo_chave, specs). `marca` = a do anúncio (`marca_do_anuncio`);
    sem ela, só pelo nome da marca no título.
    """
    if not titulo:
        return None
    marca = marca or marca_do_anuncio(titulo)
    if marca not in SITES:
        return None
    da_marca = [p for p in paginas if getattr(p, "marca", None) == marca]
    if marca == URANYX:
        return _casar_uranyx(titulo, da_marca)
    return _casar_charlots(titulo, da_marca)


async def casar_anuncio_com_site(
    session: AsyncSession,
    titulo: str | None,
    marca: str | None = None,
    *,
    agora: datetime | None = None,
) -> AtendimentoSiteProduto | None:
    """A linha de `atendimento_site_produtos` do produto do anúncio — ou None.

    Só páginas relidas nos últimos `MAX_DIAS_SEM_LER` dias. `marca` = a do
    anúncio (`marca_do_anuncio`, pelo título ou pelos SKUs).
    """
    marca = marca or marca_do_anuncio(titulo)
    if not titulo or marca not in SITES:
        return None
    return casar(titulo, await paginas_da_marca(session, marca, agora=agora), marca)


async def paginas_da_marca(
    session: AsyncSession, marca: str | None, *, agora: datetime | None = None
) -> list[AtendimentoSiteProduto]:
    """As páginas da marca relidas nos últimos `MAX_DIAS_SEM_LER` dias ([] sem marca do site).

    Uma leitura só para casar vários nomes (os produtos de um anúncio,
    `especificacoes.pagina_dos_produtos`).
    """
    if marca not in SITES:
        return []
    limite = (agora or datetime.now(UTC)) - timedelta(days=MAX_DIAS_SEM_LER)
    return list(
        (
            await session.scalars(
                select(AtendimentoSiteProduto)
                .where(
                    AtendimentoSiteProduto.marca == marca,
                    AtendimentoSiteProduto.lido_em >= limite,
                )
                .order_by(AtendimentoSiteProduto.slug)
            )
        ).all()
    )
