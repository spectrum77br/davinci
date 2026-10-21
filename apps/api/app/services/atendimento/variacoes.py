"""AS VARIAÇÕES DO ANÚNCIO para o AGENTE (20/10/2026).

O caso: na Shopee da Kia o comprador do "Uranyx A18 Pro Max NFC 16.128 Gb
Memoria S5 Premium" (anúncio 58265493964, variações dg089 / dg088 — e, desde
20/10, três do C17) perguntou "Vc tem o bordo" e "E fone vem junto". A ficha
só tinha título, atributos e descrição; a IA disse que não tinha como
confirmar a cor e foi para pessoa. A equipe respondeu "no momento não temos
na cor bordo!". Em 30 dias, `estoque_variacao` foi para pessoa 11 de 11
vezes: faltava o dado.

SÓ O AGENTE (decisão 1 do dono, 20/10/2026): o legado (Groq, prompt v10) fica
como em origin/main — a ficha que ele vê NÃO ganha variações. Elas entram só
por função nova (`variacoes_do_anuncio`, a ferramenta `ver_anuncio`) ou pelo
opt-in `com_variacoes=True` de `ficha.ficha_do_anuncio`/`fichas_da_conversa`
(o pacote do agente). O cache da loja (`chave_variacoes`) é gravado pelo
enriquecimento e pela busca da ficha da MESMA resposta da loja, sem ida nova.

O QUE O AGENTE VÊ (`variacoes_do_anuncio` → `para_o_agente`):
  {"variacoes": [{"nome", "disponivel" (true/false/null), "confiavel",
                  "fonte", "selecionada" (só a do cartão/pedido)}],
   "completas": true só quando a lista é a que a LOJA devolveu (cache de
                até 30 min, sem vínculo mais novo que ela) e nada ficou de
                fora (teto ou variação sem nome),
   "omitidas": quantas ficaram de fora}
NUNCA a quantidade (decisão 2 do dono: só disponível ou esgotado) nem data
de reposição (não existe), nem o sku e o variation_id (spec, seção 4.2:
ficam no REGISTRO, `registro_do_anuncio`, para a conferência).

DE ONDE:
  1. a LOJA, no Redis (`chave_variacoes`, `CACHE_VARIACOES_S`): o
     enriquecimento do cartão grava o `get_model_list` da Shopee (que ele já
     pedia para o preço) e as `variations` do `/items` do ML; a busca da
     ficha na loja (`ficha_loja.py`) grava os `skus` da TikTok e as
     `variations` do ML da MESMA resposta. Vínculo criado depois da ida à
     loja (variação nova): a lista do cache não vale. Sem ela, com `loja`
     (a ferramenta; no pacote, só com pergunta de cor, variação ou estoque),
     vai à loja SÓ pelas variações (`ficha_loja.variacoes_da_loja`, com cota
     e espera PRÓPRIAS — não tira vez da ficha do legado). Anúncio fora do ar
     na loja (pausado, em revisão, banido, apagado: `_anuncio_parado`) e
     modelo fora do ar entram esgotados;
  2. os VÍNCULOS da loja da conversa (`product_links` vivos + `products`).

NOME, de UMA fonte só para a lista inteira (revisão de 20/10/2026: em
produção, 106 dos 449 anúncios multivariação da Shopee saíam com nomes de
fontes misturadas, 71 com o nome tirado de título velho e 9 com nomes
repetidos): o da loja (as opções do `tier_variation` da Shopee, os
`sales_attributes` da TikTok, as `attribute_combinations` do ML); sem a
lista da loja, o final do título do vínculo (`listing_title` = "título -
variação") só quando TODOS os vínculos começam pelo título ATUAL do anúncio
(o da conversa ou o da loja — nunca o do catálogo, que pode ser de antes da
renomeação) e os finais são distintos; fora disso, `products.name` (a ordem
da spec) sem o começo comum e sem o código entre parênteses. Nome repetido:
sem lista. No caso da Kia o Bling diz "Prata + Fone UFB10" e o anúncio "A18
- Branco NFC": o cliente vê o do anúncio.

DISPONÍVEL: o estoque da LOJA quando a lista dela está no cache (o que o
cliente consegue comprar agora); sem ela, o do VÍNCULO (`product_links.stock`,
o que o DaVinci mandou ou confirmou por último no anúncio). Variação com dois
vínculos (kit): disponível só com todos. Anúncio fora do ar pelo vínculo
(`anuncio_status`, só o ML grava) e sem a lista da loja: esgotado.
CONFIÁVEL (spec, seção 4.2): vínculo vivo com `last_sync_status='ok'`, o
anúncio no ar e `(vínculo > 0) == (produto > 0)`; com a lista da loja, ela
também tem de bater com o vínculo. Sem vínculo (ML Full, catálogo, anúncio
não vinculado): nunca. E nunca com o nome do CADASTRO (o Bling diz "Prata",
o anúncio "Branco": afirmar a opção pelo nome do Bling pode ser errado).
Com `confiavel=false`, o agente diz que as opções aparecem no anúncio.

A CONFERÊNCIA (`conferir_variacoes`, para as checagens do agente), contra a
lista do anúncio de que o cliente fala (a 1ª ficha — a mais recente; nunca
as listas de dois anúncios misturadas):
  - afirmar que TEM uma variação exige disponivel=true e confiavel=true;
    dizer que ACABOU exige disponivel=false e confiavel=true;
  - opção fora da lista: "não temos" só com a lista COMPLETA e do MESMO tipo
    (cor numa lista de cores; memória numa lista de memórias); "temos"
    nunca. Memória/cor que o anúncio não varia mas está no título ou nos
    atributos fala do anúncio inteiro ("não temos 128 GB" no A18 16.128
    bloqueia). Opção que a lista não cobre: pessoa;
  - "temos sim!" sem citar a opção vale para a opção que o CLIENTE citou
    (ou, sem ela, para as que a resposta cita sem dizer se tem), em
    qualquer frase — mesmo que outra frase cite uma cor;
  - contradiz o dado → BLOQUEADO (como número inventado); sem dado para
    conferir → pessoa (pendente com o motivo);
  - sem `estoque_aprovado`, QUALQUER afirmação de estoque vai para pessoa
    (`MOTIVO_ESTOQUE_SEM_REGRA`); com variações de dois anúncios, também
    (`MOTIVO_DUAS_FICHAS`);
  - nunca "últimas unidades", reserva ou reposição prometida ("não temos
    data de reposição" não é promessa), nem o sku/variation_id no texto.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Product, ProductLink
from app.models.enums import IntegrationPlatform, LinkSyncStatus
from app.services.atendimento import ficha as ficha_svc
from app.services.atendimento.validador import plano_de

if TYPE_CHECKING:
    from app.services.atendimento.ficha_loja import Busca

logger = structlog.get_logger()

# ── Tetos e validade ──────────────────────────────────────────────────────

# O estoque muda: a lista da loja vale 30 min (spec, seção 4.2).
CACHE_VARIACOES_S = 30 * 60
# Por anúncio: o primeiro (o que o cliente perguntou) e o segundo.
MAX_VARIACOES_FICHA = 12
MAX_VARIACOES_SEGUNDA_FICHA = 6
MAX_CHARS_NOME_VARIACAO = 60
# Vínculos lidos por anúncio (a TikTok chega a 52 variações).
MAX_VINCULOS_LIDOS = 120
# Vínculo criado depois da ida à loja (variação nova no anúncio): a lista do
# cache deixa de valer. A folga cobre a hora do cache, gravada em segundos, e
# o relógio do banco.
FOLGA_VINCULO_NOVO = timedelta(minutes=1)

# As chaves que a ficha do agente ganha (`ficha.ficha_do_anuncio(com_variacoes=True)`;
# o `ficha.caber` as deixa fora do teto do texto).
CHAVES_DA_FICHA = ("variacoes", "variacoes_completas", "variacoes_omitidas")

# Os assuntos de variação (a conferência vale para eles).
CATEGORIAS_DE_VARIACAO = frozenset({"estoque_variacao", "confirmar_variacao"})

# De onde veio o NOME da lista (`fonte` de cada variação). Só o da loja e o
# final do título ATUAL são o que o cliente vê; o do cadastro nunca é confiável.
FONTE_LOJA = "loja"
FONTE_ANUNCIO = "titulo_do_vinculo"
FONTE_CADASTRO = "cadastro"
FONTES_CONFIAVEIS = frozenset({FONTE_LOJA, FONTE_ANUNCIO})

# O que fica só no REGISTRO (spec, 4.2): nunca vai ao modelo.
_SO_NO_REGISTRO = ("sku", "variation_id")
# A ordem das chaves de cada variação para o agente.
_DO_AGENTE = ("nome", "disponivel", "confiavel", "fonte")

_SHOPEE_MODELO_ATIVO = frozenset({"MODEL_NORMAL", "NORMAL"})
# Anúncio no ar: ML "active", Shopee "NORMAL", TikTok "ACTIVATE". Pausado,
# em revisão, banido, apagado, fora de venda: esgotado. Produção, 20/10/2026:
# no ML, 2.615 vínculos vivos `paused` e 2.924 `under_review`.
_ANUNCIO_ATIVO = frozenset({"active", "normal", "activate", "live"})


# ── Conversões ────────────────────────────────────────────────────────────


def _texto(valor: Any) -> str | None:
    if valor is None or isinstance(valor, bool):
        return None
    if isinstance(valor, int | float):
        valor = str(valor)
    if not isinstance(valor, str):
        return None
    valor = " ".join(valor.split())
    return valor or None


def _inteiro(valor: Any) -> int | None:
    if isinstance(valor, bool):
        return None
    if isinstance(valor, int):
        return valor
    if isinstance(valor, float) and valor.is_integer():
        return int(valor)
    if isinstance(valor, str) and valor.strip().lstrip("-").isdigit():
        return int(valor.strip())
    return None


def _lista(bruto: Any) -> list[dict]:
    return [x for x in bruto if isinstance(x, dict)] if isinstance(bruto, list) else []


def _dict(bruto: Any) -> dict:
    return bruto if isinstance(bruto, dict) else {}


def _plano(texto: Any) -> str:
    return " ".join(plano_de(str(texto or "")).split())


def _anuncio_parado(status: Any) -> bool:
    """O status do anúncio diz que ele está fora de venda? (sem status: não se sabe → False)"""
    s = (_texto(status) or "").lower()
    return bool(s) and s not in _ANUNCIO_ATIVO


def _variacao(variation_id: Any, nome: Any, sku: Any, estoque: int | None) -> dict | None:
    vid = _texto(variation_id)
    if vid is None or len(vid) > 64:
        return None
    return {"variation_id": vid, "nome": _texto(nome), "sku": _texto(sku), "estoque": estoque}


def _parado(saida: list[dict], status: Any) -> list[dict]:
    """Anúncio fora do ar na loja: todas as variações entram esgotadas."""
    return [{**v, "estoque": 0} for v in saida] if _anuncio_parado(status) else saida


# ── O que a loja devolve → [{variation_id, nome, sku, estoque}] (puras) ───


def _estoque_shopee(modelo: dict) -> int | None:
    resumo = _dict(_dict(modelo.get("stock_info_v2")).get("summary_info"))
    total = _inteiro(resumo.get("total_available_stock"))
    if total is None:
        total = _inteiro(_dict(modelo.get("stock_info")).get("total_available_stock"))
    return total


def da_shopee(resposta: Any, item_status: Any = None) -> list[dict]:
    """As variações do `get_model_list` da Shopee. PURA.

    Nome = as opções do `tier_variation` pelo `tier_index` ("A18, Branco
    NFC"); sem elas, o `model_name` (que a Shopee separa por vírgula).
    Modelo fora do ar (`model_status` diferente de normal) entra esgotado; o
    anúncio inteiro também, com o `item_status` (do `get_item_base_info`:
    o `get_model_list` não o traz) diferente de NORMAL.
    """
    r = _dict(resposta)
    opcoes = [
        [_texto(_dict(o).get("option")) for o in _lista(t.get("option_list"))]
        for t in _lista(r.get("tier_variation"))
    ]
    saida: list[dict] = []
    for m in _lista(r.get("model")):
        partes: list[str] = []
        indices = m.get("tier_index") if isinstance(m.get("tier_index"), list) else []
        for nivel, i in enumerate(indices):
            if isinstance(i, int) and nivel < len(opcoes) and 0 <= i < len(opcoes[nivel]):
                if opcao := opcoes[nivel][i]:
                    partes.append(opcao)
        nome = ", ".join(partes) or ", ".join(
            p.strip() for p in (_texto(m.get("model_name")) or "").split(",") if p.strip()
        )
        estoque = _estoque_shopee(m)
        status = (_texto(m.get("model_status")) or "").upper()
        if status and status not in _SHOPEE_MODELO_ATIVO:
            estoque = 0
        if v := _variacao(m.get("model_id"), nome, m.get("model_sku"), estoque):
            saida.append(v)
    return _parado(saida, item_status)


def da_tiktok(data: Any) -> list[dict]:
    """As variações (`skus`) do produto da TikTok (Get Product 202309). PURA.

    Nome = os `sales_attributes[].value_name`; estoque = a soma do
    `inventory[].quantity` dos armazéns (None sem `inventory`). Produto com
    `status` diferente de ACTIVATE: tudo esgotado.
    """
    saida: list[dict] = []
    for s in _lista(_dict(data).get("skus")):
        nome = ", ".join(
            v for a in _lista(s.get("sales_attributes")) if (v := _texto(a.get("value_name")))
        )
        quantidades = [_inteiro(i.get("quantity")) for i in _lista(s.get("inventory"))]
        validas = [q for q in quantidades if q is not None]
        estoque = sum(validas) if validas else None
        if v := _variacao(s.get("id"), nome, s.get("seller_sku"), estoque):
            saida.append(v)
    return _parado(saida, _dict(data).get("status"))


def _sku_ml(variacao: dict) -> str | None:
    for a in _lista(variacao.get("attributes")):
        if (_texto(a.get("id")) or "").upper() == "SELLER_SKU":
            if sku := _texto(a.get("value_name")) or _texto(a.get("value")):
                return sku
    return _texto(variacao.get("seller_custom_field"))


def da_ml(body: Any) -> list[dict]:
    """As `variations` do anúncio do ML (`/items`). PURA.

    Nome = as `attribute_combinations[].value_name`; estoque =
    `available_quantity`. Anúncio sem variação: []. Anúncio com `status`
    diferente de active (pausado, em revisão): tudo esgotado.
    """
    saida: list[dict] = []
    for v in _lista(_dict(body).get("variations")):
        nome = ", ".join(
            x for a in _lista(v.get("attribute_combinations")) if (x := _texto(a.get("value_name")))
        )
        if item := _variacao(v.get("id"), nome, _sku_ml(v), _inteiro(v.get("available_quantity"))):
            saida.append(item)
    return _parado(saida, _dict(body).get("status"))


# ── Redis ─────────────────────────────────────────────────────────────────


def chave_variacoes(plataforma: str, integration_id: Any, item_id: str) -> str:
    """A lista da loja — o mesmo recorte da ficha fresca (`ficha.chave_ficha`)."""
    return f"atd:variacoes:{plataforma}:{integration_id}:{item_id}"


def para_o_cache(variacoes: list[dict], agora: datetime | None = None) -> str:
    """O valor da chave: a lista e a hora da ida à loja."""
    quando = (agora or datetime.now(UTC)).isoformat(timespec="seconds")
    return json.dumps({"variacoes": variacoes, "buscada_em": quando}, ensure_ascii=False)


def _redis():
    # O MESMO cliente da ficha (`ficha.redis`): os testes trocam num lugar só.
    return ficha_svc.redis


def _quando(valor: Any) -> datetime | None:
    try:
        quando = datetime.fromisoformat(str(valor))
    except (TypeError, ValueError):
        return None
    return quando if quando.tzinfo is not None else None


async def da_loja_em_cache(
    plataforma: IntegrationPlatform, integration_id: UUID | None, item_id: str
) -> tuple[list[dict], datetime | None] | None:
    """(a lista que a loja devolveu, a hora da ida) — até 30 min; ou None
    (sem ela, sem Redis, ilegível)."""
    if integration_id is None:
        return None
    try:
        bruto = await _redis().get(chave_variacoes(plataforma.value, integration_id, item_id))
    except Exception as exc:  # noqa: BLE001 — sem Redis, fica com os vínculos
        logger.info("atendimento_variacoes_cache_indisponivel", erro=type(exc).__name__)
        return None
    if not bruto:
        return None
    try:
        dado = json.loads(bruto)
    except (TypeError, ValueError):
        return None
    lista = _dict(dado).get("variacoes")
    if not isinstance(lista, list):
        return None
    return _lista(lista), _quando(_dict(dado).get("buscada_em"))


# ── Os vínculos (banco) ───────────────────────────────────────────────────


@dataclass(frozen=True)
class Vinculo:
    variation_id: str | None
    sku: str | None
    titulo: str | None  # `listing_title`: "título do anúncio - variação"
    estoque: int | None  # `product_links.stock`
    status: str | None  # `last_sync_status`
    nome_produto: str | None
    estoque_produto: int | None
    anuncio_status: str | None = None  # `anuncio_status` (só o ML grava)
    criado_em: datetime | None = None  # `created_at`: vínculo mais novo que a lista da loja


async def vinculos_do_anuncio(
    session: AsyncSession,
    plataforma: IntegrationPlatform,
    integration_id: UUID | None,
    item_id: str,
) -> list[Vinculo]:
    """Os vínculos VIVOS do anúncio na loja da conversa, com o produto do Bling."""
    if integration_id is None:
        return []
    linhas = (
        await session.execute(
            select(
                ProductLink.variation_id,
                ProductLink.external_sku,
                ProductLink.listing_title,
                ProductLink.stock,
                ProductLink.last_sync_status,
                Product.name,
                Product.stock,
                Product.sku,
                ProductLink.anuncio_status,
                ProductLink.created_at,
            )
            .join(Product, Product.id == ProductLink.product_id)
            .where(
                ProductLink.integration_id == integration_id,
                ProductLink.platform == plataforma,
                ProductLink.external_id == item_id,
                ProductLink.morto_desde.is_(None),
            )
            .order_by(ProductLink.created_at, ProductLink.variation_id)
            .limit(MAX_VINCULOS_LIDOS)
        )
    ).all()
    return [
        Vinculo(
            variation_id=_texto(r[0]),
            sku=_texto(r[1]) or _texto(r[7]),
            titulo=_texto(r[2]),
            estoque=r[3],
            status=str(getattr(r[4], "value", r[4])) if r[4] is not None else None,
            nome_produto=_texto(r[5]),
            estoque_produto=r[6],
            anuncio_status=_texto(r[8]),
            criado_em=r[9],
        )
        for r in linhas
    ]


# ── Montagem (pura) ───────────────────────────────────────────────────────

_SEPARADOR_TITULO = " - "
# Os RÓTULOS INTERNOS do cadastro, que nunca vão ao modelo (revisão de
# 20/10/2026: 60 vínculos vivos da Shopee apontam para "… AVULSO SALVADO" —
# a unidade devolvida, num anúncio "Original Lacrado"): o código de
# fornecedor entre parênteses em qualquer lugar do nome, "avulso",
# "salvado", "avariado", "fake", "usado" e "lote N".
_ROTULOS_INTERNOS = re.compile(
    r"\([^()]{0,80}\)"
    r"|\b(?:avuls[oa]s?|salvad[oa]s?|avariad[oa]s?|fakes?|usad[oa]s?|lote\s*\d+)\b",
    re.IGNORECASE,
)


def sem_rotulos_internos(nome: str | None) -> str | None:
    """O nome do cadastro sem os rótulos internos (`_ROTULOS_INTERNOS`). PURA."""
    if not nome:
        return None
    limpo = " ".join(_ROTULOS_INTERNOS.sub(" ", nome).split())
    return limpo.strip(" -–,;/") or None


@dataclass
class _Juntada:
    variation_id: str | None
    da_loja: dict | None = None
    vinculos: list[Vinculo] = field(default_factory=list)
    nome: str | None = None


def _distintos(nomes: Sequence[str | None]) -> bool:
    planos = [_plano(n) for n in nomes if n]
    return len(planos) == len(nomes) and len(set(planos)) == len(planos)


def _finais_do_titulo(juntadas: list[_Juntada], titulo_anuncio: str | None) -> list[str] | None:
    """O final do `listing_title` de cada variação ("… - Branco NFC") — ou None.

    Só quando TODOS os vínculos com título começam pelo título ATUAL do
    anúncio + " - " e os finais são distintos (revisão de 20/10/2026: na
    Kia, 5 dos 6 vínculos do S7 21599835776 ainda tinham o título de antes
    da renomeação, "…16.128 GB ou 32.256 GB…").
    """
    base = _texto(titulo_anuncio)
    if not base:
        return None
    prefixo = base + _SEPARADOR_TITULO
    saida: list[str | None] = []
    for j in juntadas:
        finais = {
            _texto(v.titulo[len(prefixo) :]) if v.titulo.startswith(prefixo) else None
            for v in j.vinculos
            if v.titulo
        }
        if len(finais) != 1 or None in finais:
            return None
        saida.append(finais.pop())
    return saida if _distintos(saida) else None  # type: ignore[return-value]


def _nomes_dos_produtos(nomes: list[str | None]) -> list[str | None]:
    """`products.name` sem os rótulos internos (`sem_rotulos_internos`: o código
    entre parênteses, "avulso", "salvado"...) e sem o começo comum até o último " - "."""
    limpos = [_texto(sem_rotulos_internos(n)) if n else None for n in nomes]
    validos = [n for n in limpos if n]
    if len(validos) >= 2 and len(validos) == len(limpos):
        comum = os.path.commonprefix(validos)
        corte = comum.rfind(_SEPARADOR_TITULO)
        if corte > 0:
            inicio = corte + len(_SEPARADOR_TITULO)
            restos = [_texto(n[inicio:]) for n in validos]
            if all(restos):
                return restos
    return limpos


def _cortar_nome(nome: str | None) -> str | None:
    if not nome:
        return None
    if len(nome) <= MAX_CHARS_NOME_VARIACAO:
        return nome
    return nome[: MAX_CHARS_NOME_VARIACAO - 1].rstrip(" ,;:-") + "…"


def _disponivel_e_confiavel(j: _Juntada) -> tuple[bool | None, bool]:
    """(disponível, confiável) de UMA variação — ver o topo. PURA.

    O nome do cadastro tira o confiável depois (`montar_variacoes`).
    """
    estoque_loja = _inteiro((j.da_loja or {}).get("estoque"))
    parado = any(_anuncio_parado(v.anuncio_status) for v in j.vinculos)
    do_vinculo: bool | None = None
    coerente = False
    if j.vinculos:
        estoques = [v.estoque for v in j.vinculos]
        if all(e is not None for e in estoques):
            do_vinculo = all(e > 0 for e in estoques)  # type: ignore[operator]
            coerente = not parado and all(
                v.status == LinkSyncStatus.OK.value
                and v.estoque_produto is not None
                and (v.estoque > 0) == (v.estoque_produto > 0)  # type: ignore[operator]
                for v in j.vinculos
            )
    if estoque_loja is not None:
        # A loja manda (o que o cliente compra agora); o vínculo só confirma.
        disponivel = estoque_loja > 0
        return disponivel, coerente and do_vinculo == disponivel
    if parado:
        # Fora do ar pelo vínculo, sem a loja para dizer o contrário.
        return False, False
    return do_vinculo, coerente


def _mais_novo_que_a_lista(vinculos: Sequence[Vinculo], buscada_em: datetime | None) -> bool:
    """Algum vínculo nasceu depois da ida à loja? (a lista pode não ter a variação nova)"""
    if buscada_em is None:
        return False
    try:
        limite = buscada_em + FOLGA_VINCULO_NOVO
        return any(v.criado_em is not None and v.criado_em > limite for v in vinculos)
    except TypeError:  # data sem fuso: não se sabe
        return True


def montar_variacoes(
    da_loja: list[dict] | None,
    vinculos: Sequence[Vinculo],
    *,
    titulo: str | None = None,
    variation_id: str | None = None,
    palavras: frozenset[str] = frozenset(),
    teto: int = MAX_VARIACOES_FICHA,
    buscada_em: datetime | None = None,
) -> dict | None:
    """O REGISTRO das variações — ou None (anúncio sem escolha de variação). PURA.

    `{"variacoes": [{nome, fonte, disponivel?, confiavel, sku?, variation_id?,
    selecionada?}], "variacoes_completas": bool, "variacoes_omitidas"?: int}`.
    Com a lista da loja, ela manda: variação que a loja não tem mais fica de
    fora mesmo com vínculo vivo, e a que veio sem nome conta como omitida.
    Sem ela, os vínculos. Menos de 2 variações, ou nomes repetidos: None.
    No teto, ficam primeiro a selecionada, as que batem com a pergunta e as
    disponíveis confiáveis — na ordem do anúncio. `titulo` = o título ATUAL
    do anúncio. `buscada_em` = a hora da lista da loja no cache (vínculo
    mais novo que ela: a lista não é completa).
    """
    por_id: dict[str | None, _Juntada] = {}
    ordem: list[str | None] = []
    if da_loja is not None:
        for item in da_loja:
            vid = _texto(item.get("variation_id"))
            if vid is None or vid in por_id:
                continue
            por_id[vid] = _Juntada(vid, da_loja=item)
            ordem.append(vid)
    for v in vinculos:
        if v.variation_id not in por_id:
            if da_loja is not None:
                continue  # a loja não tem mais essa variação
            por_id[v.variation_id] = _Juntada(v.variation_id)
            ordem.append(v.variation_id)
        por_id[v.variation_id].vinculos.append(v)
    juntadas = [por_id[vid] for vid in ordem]
    if len(juntadas) < 2:
        return None

    # O nome, de UMA fonte para a lista inteira (ver o topo).
    if da_loja is not None:
        fonte = FONTE_LOJA
        nomes: list[str | None] = [_texto((j.da_loja or {}).get("nome")) for j in juntadas]
    elif finais := _finais_do_titulo(juntadas, titulo):
        fonte, nomes = FONTE_ANUNCIO, list(finais)
    else:
        fonte = FONTE_CADASTRO
        nomes = _nomes_dos_produtos(
            [j.vinculos[0].nome_produto if j.vinculos else None for j in juntadas]
        )
        if not all(nomes):
            return None
    for j, nome in zip(juntadas, nomes, strict=True):
        j.nome = _cortar_nome(nome)
    com_nome = [j for j in juntadas if j.nome]
    if len(com_nome) < 2 or not _distintos([j.nome for j in com_nome]):
        return None

    linhas: list[dict] = []
    for j in com_nome:
        disponivel, confiavel = _disponivel_e_confiavel(j)
        sku = _texto((j.da_loja or {}).get("sku")) or (j.vinculos[0].sku if j.vinculos else None)
        linha: dict[str, Any] = {"nome": j.nome, "fonte": fonte}
        if disponivel is not None:
            linha["disponivel"] = disponivel
        linha["confiavel"] = bool(
            confiavel and disponivel is not None and fonte in FONTES_CONFIAVEIS
        )
        if sku:
            linha["sku"] = sku
        if j.variation_id:
            linha["variation_id"] = j.variation_id
        if variation_id is not None and j.variation_id == variation_id:
            linha["selecionada"] = True
        linhas.append(linha)

    def _peso(i: int) -> tuple[int, int]:
        linha = linhas[i]
        if linha.get("selecionada"):
            return (0, i)
        if palavras and ficha_svc._bate(palavras, _plano(linha["nome"])):
            return (1, i)
        if linha.get("disponivel") and linha["confiavel"]:
            return (2, i)
        return (3, i)

    escolhidas = sorted(sorted(range(len(linhas)), key=_peso)[: max(teto, 0)])
    # A variação da loja sem nome também ficou de fora: a lista não é completa.
    omitidas = len(juntadas) - len(escolhidas)
    bloco: dict[str, Any] = {
        "variacoes": [linhas[i] for i in escolhidas],
        "variacoes_completas": da_loja is not None
        and omitidas == 0
        and not _mais_novo_que_a_lista(vinculos, buscada_em),
    }
    if omitidas:
        bloco["variacoes_omitidas"] = omitidas
    return bloco


def para_o_agente(registro: Mapping[str, Any] | None) -> dict | None:
    """O REGISTRO como o AGENTE o vê — ou None. PURA.

    `{"variacoes": [{nome, disponivel, confiavel, fonte, selecionada?}],
    "completas", "omitidas"}`: nunca o sku nem o variation_id (spec, 4.2),
    nunca a quantidade; `disponivel` null = não se sabe.
    """
    if not isinstance(registro, Mapping) or not isinstance(registro.get("variacoes"), list):
        return None
    variacoes: list[dict] = []
    for v in registro["variacoes"]:
        if not isinstance(v, Mapping) or not _texto(v.get("nome")):
            continue
        linha = {k: v.get(k) for k in _DO_AGENTE}
        linha["confiavel"] = v.get("confiavel") is True
        if v.get("selecionada") is True:
            linha["selecionada"] = True
        variacoes.append(linha)
    if not variacoes:
        return None
    omitidas = _inteiro(registro.get("variacoes_omitidas")) or 0
    return {
        "variacoes": variacoes,
        "completas": registro.get("variacoes_completas") is True,
        "omitidas": max(omitidas, 0),
    }


def na_ficha(bloco: Mapping[str, Any] | None) -> dict:
    """O bloco do agente nas chaves da ficha (`CHAVES_DA_FICHA`) — {} sem ele. PURA."""
    if not isinstance(bloco, Mapping) or not bloco.get("variacoes"):
        return {}
    saida: dict[str, Any] = {
        "variacoes": list(bloco["variacoes"]),
        "variacoes_completas": bloco.get("completas") is True,
    }
    if omitidas := _inteiro(bloco.get("omitidas")):
        saida["variacoes_omitidas"] = omitidas
    return saida


# ── Quando ir à loja ──────────────────────────────────────────────────────

# Cores (sem acento, minúsculas). "natural", "creme" e outras palavras que
# também são de produto ficam de fora.
_CORES = frozenset(
    """
    preto preta pretos pretas branco branca brancos brancas prata prateado prateada
    dourado dourada ouro gold rosa rose pink azul azuis verde verdes vermelho vermelha
    vermelhos vermelhas amarelo amarela amarelos amarelas laranja roxo roxa roxos roxas
    lilas violeta cinza grafite chumbo bege marrom nude vinho bordo marsala caramelo
    titanio champanhe champagne cobre bronze coral turquesa lavanda mostarda areia
    black white silver blue green red purple gray grey
    """.split()
)


def _raiz(palavra: str) -> str:
    """A forma comparável da palavra: "branca"/"brancos" → "branc"."""
    if len(palavra) > 4 and palavra.endswith("s"):
        palavra = palavra[:-1]
    if len(palavra) > 3 and palavra[-1] in "aoe":
        palavra = palavra[:-1]
    return palavra


# Cor que também é coisa: "mala de bordo", "adega de vinho", "banhado a
# ouro". Depois de "de"/"a" não é cor — a não ser "cor de vinho".
_CORES_AMBIGUAS = frozenset(
    "bordo vinho ouro laranja areia coral cobre bronze caramelo chumbo champanhe champagne".split()
)
_ANTES_DA_COISA = frozenset({"de", "do", "da", "a", "ao"})
_PALAVRA = re.compile(r"[a-z0-9]+")


def _cor_em(palavras: Sequence[str], i: int) -> str | None:
    """A chave da cor na posição `i` ("cor:" + raiz), ou None (não é cor ali)."""
    p = palavras[i]
    if p not in _CORES:
        return None
    if (
        p in _CORES_AMBIGUAS
        and i > 0
        and palavras[i - 1] in _ANTES_DA_COISA
        and not (i > 1 and palavras[i - 2] in ("cor", "cores", "tom"))
    ):
        return None
    return "cor:" + _raiz(p)


_UNIDADE = re.compile(r"(\d)\s+(gb|tb|mb|mm|cm|ml|kg|v|w)\b")

_PEDE_VARIACAO = re.compile(
    r"\b(?:cor|cores|variac\w*|opc(?:ao|oes)|modelo\w*|versao|versoes|tamanho\w*|memoria\w*"
    r"|voltagem|bivolt|estoque|disponive\w*|disponibilidade|esgot\w*|acab\w*|ainda\s+tem"
    r"|tem\s+ainda|vende\w*|repos\w*|repor)\b|\d+\s*(?:gb|tb|v)\b"
)


def _normal(texto: Any) -> str:
    return _UNIDADE.sub(r"\1\2", _plano(texto))


def pede_variacao(textos: Iterable[str | None]) -> bool:
    """O cliente fala de cor, variação ou estoque? (o pacote: ir à loja pelas variações). PURA."""
    plano = _normal(" ".join(t for t in textos if t))
    if _PEDE_VARIACAO.search(plano):
        return True
    palavras = _PALAVRA.findall(plano)
    return any(_cor_em(palavras, i) for i in range(len(palavras)))


def _vale_ir_a_loja(vinculos: Sequence[Vinculo]) -> bool:
    """O anúncio pode ter variação? (um vínculo só, sem variação: não tem — não vai)"""
    return len(vinculos) != 1 or vinculos[0].variation_id is not None


async def registro_do_anuncio(
    session: AsyncSession,
    plataforma: IntegrationPlatform,
    integration_id: UUID | None,
    item_id: str,
    *,
    loja: Busca | None = None,
    titulo: str | None = None,
    variation_id: str | None = None,
    palavras: frozenset[str] = frozenset(),
    teto: int = MAX_VARIACOES_FICHA,
) -> dict | None:
    """O REGISTRO das variações de UM anúncio da loja da conversa (`montar_variacoes`) — ou None.

    Com o sku e o variation_id: só para a conferência e o rastro, NUNCA para
    o modelo (`para_o_agente`). A lista da loja vem do cache — a não ser que
    um vínculo seja mais novo que ela; sem ela e com `loja`, vai à loja SÓ
    pelas variações (`ficha_loja.variacoes_da_loja`, cota própria), a não
    ser que o anúncio tenha um vínculo só, sem variação. `titulo` = o título
    ATUAL do anúncio (o da conversa ou o da loja; nunca o do catálogo).
    Problema da loja ou do Redis é "sem a lista da loja" (fica com os
    vínculos); erro de BANCO sobe.
    """
    if integration_id is None:
        return None
    vinculos = await vinculos_do_anuncio(session, plataforma, integration_id, item_id)
    em_cache = await da_loja_em_cache(plataforma, integration_id, item_id)
    da_loja, buscada_em = em_cache if em_cache is not None else (None, None)
    if da_loja is not None and _mais_novo_que_a_lista(vinculos, buscada_em):
        # Variação nova depois da ida à loja: a lista do cache não a tem.
        da_loja, buscada_em = None, None
    if da_loja is None and loja is not None and _vale_ir_a_loja(vinculos):
        from app.services.atendimento import ficha_loja

        da_loja = await ficha_loja.variacoes_da_loja(session, loja, plataforma, item_id)
    return montar_variacoes(
        da_loja,
        vinculos,
        titulo=titulo,
        variation_id=variation_id,
        palavras=palavras,
        teto=teto,
        buscada_em=buscada_em,
    )


async def variacoes_do_anuncio(
    session: AsyncSession,
    plataforma: IntegrationPlatform,
    integration_id: UUID | None,
    item_id: str,
    *,
    loja: Busca | None = None,
    titulo: str | None = None,
    variation_id: str | None = None,
    palavras: frozenset[str] = frozenset(),
    teto: int = MAX_VARIACOES_FICHA,
) -> dict | None:
    """As variações de UM anúncio como o AGENTE as vê (`para_o_agente`) — ou None.

    `{"variacoes": [{nome, disponivel, confiavel, fonte, selecionada?}],
    "completas", "omitidas"}` — sem quantidade, sku nem variation_id. Os
    mesmos parâmetros e a mesma origem de `registro_do_anuncio`.
    """
    return para_o_agente(
        await registro_do_anuncio(
            session,
            plataforma,
            integration_id,
            item_id,
            loja=loja,
            titulo=titulo,
            variation_id=variation_id,
            palavras=palavras,
            teto=teto,
        )
    )


# ── A conferência da resposta (pura) ──────────────────────────────────────

# Sem a liberação do estoque (`estoque_aprovado`): a regra vigente do Manual
# (estoque_variacao) manda conferir no anúncio "sem afirmar que há ou que
# falta estoque" (revisão de 20/10/2026). O agente passa a liberação pela
# decisão 2 do dono (só disponível ou esgotado); quem não passa, fica nela.
MOTIVO_ESTOQUE_SEM_REGRA = (
    "diz se a variação tem ou não estoque — o Manual (estoque_variacao) manda conferir no anúncio"
)
MOTIVO_DUAS_FICHAS = "fala de estoque com variações de dois anúncios — não dá para saber de qual"


@dataclass(frozen=True)
class Pendencia:
    motivo: str
    # True = contradiz o dado: rascunho bloqueado (como número inventado).
    # False = sem dado para conferir: vai para pessoa (pendente).
    bloqueia: bool


@dataclass(frozen=True)
class _Opcao:
    nome: str
    chaves: frozenset[str]
    disponivel: bool | None
    confiavel: bool
    codigos: frozenset[str] = frozenset()  # sku e variation_id (nunca para o cliente)


@dataclass(frozen=True)
class _Mencao:
    inicio: int
    fim: int
    chaves: frozenset[str]
    texto: str


@dataclass(frozen=True)
class _Lista:
    opcoes: list[_Opcao]
    completa: bool
    # Os tipos de opção em que o anúncio VARIA ("cor", "mem", "volt"): só
    # neles a lista completa prova que a opção pedida não existe.
    tipos: frozenset[str] = frozenset()
    # Cor, memória e voltagem FIXAS do anúncio (título e atributos da ficha).
    fixas: frozenset[str] = frozenset()


# A palavra da conferência: número composto ("8.128", "16.128gb", "4/64") ou
# letras e dígitos ("128gb", "a18").
_TOKEN = re.compile(r"\d+(?:[.,/+]\d+)+(?:gb|tb)?|[a-z0-9]+")
# Memória escrita sem "GB" (revisão de 20/10/2026: "o dourado 8.128" não era
# citação): RAM.ROM com a ROM numa medida de memória.
_MEMORIA_COMPOSTA = re.compile(r"(\d{1,2})[./+](16|32|64|128|256|512)(?:gb)?")
_MEMORIA = re.compile(r"(\d{1,4})(gb|tb)")
_MEMORIA_SOLTA = frozenset({"32", "64", "128", "256", "512"})
_VOLTAGEM = re.compile(r"(?:110|127|220|240)v|bivolt")


def _de_opcao(p: str) -> frozenset[str]:
    """As chaves de memória ou voltagem de uma palavra ("8.128" → mem:8 e mem:128).

    RAM e ROM ficam no mesmo "mem:": "8GB + 128GB", "8/128" e "8.128" se
    reconhecem.
    """
    if m := _MEMORIA_COMPOSTA.fullmatch(p):
        return frozenset({f"mem:{int(m[1])}", f"mem:{int(m[2])}"})
    if m := _MEMORIA.fullmatch(p):
        return frozenset({f"mem:{int(m[1])}" + ("tb" if m[2] == "tb" else "")})
    if _VOLTAGEM.fullmatch(p):
        return frozenset({"volt:" + p.removesuffix("v")})
    return frozenset()


def _tipos(chaves: Iterable[str]) -> frozenset[str]:
    return frozenset(c.split(":", 1)[0] for c in chaves if ":" in c)


_SENTENCA = re.compile(r"(?:[!?;:\n]|\.(?!\d))+|\b(?:mas|porem|contudo|entretanto|todavia)\b")
# O que cabe numa lista de opções depois da vírgula ("não temos o bordô, o
# vinho nem o roxo"): aí a marca de antes da vírgula continua valendo.
_DA_LISTA = frozenset(
    "e ou nem o a os as na no nas nos em de da do cor cores tambem versao opcao".split()
)
_CONJUNCAO = frozenset({"e", "ou", "nem"})
_SEM_SENTIDO = frozenset(
    "de da do das dos com sem para pra e ou a o as os um uma em na no cor tamanho".split()
)
# "há" de tempo ("há 2 dias") não é de estoque.
_HA = (
    r"ha(?!\s+(?:\d|pouco|muito|tempo|dias?|horas?|semanas?|mes|meses|anos?|minutos?"
    r"|instantes?)\b)"
)
_MARCA = re.compile(
    rf"(?P<verbo>\b(?:temos|tem|possuimos|possui|possuem|contamos\s+com|trabalhamos\s+com"
    rf"|{_HA}|existem?|vendemos|oferecemos"
    r"|vem\s+(?:tambem\s+)?(?:na|nas|em)(?:\s+cor(?:es)?)?)\b)"
    r"|(?P<pos>\b(?:disponive(?:l|is)|em\s+estoque|com\s+estoque|a\s+pronta\s+entrega"
    r"|pode\s+(?:comprar|pedir|fazer\s+o\s+pedido|finalizar\s+a\s+compra))\b)"
    r"|(?P<neg>\b(?:esgotad[oa]s?|esgot(?:ou|aram)|indisponive(?:l|is)"
    r"|acab(?:ou|aram)(?!\s+de\b)|em\s+falta|sem\s+estoque|fora\s+de\s+estoque|zerad[oa]s?"
    r"|(?:estamos|ficamos|estou|fiquei)\s+sem)\b)"
    r"|(?P<so>\b(?:apenas|somente|so)\b)"
)
_NEGA_ANTES = re.compile(r"\b(?:nao|nem)\s+(?:[a-z0-9]+\s+){0,2}$")
_NEGA = re.compile(r"\b(?:nao|nem)\b")
# O predicado colado DEPOIS da citação (revisão de 20/10/2026: "o laranja não
# temos mais", "o bordô a gente tem sim" e "o bordô está com estoque"
# passavam): o verbo de ter só com advérbio até o fim da frase…
_TER_DEPOIS = re.compile(
    rf"\s*,?(?P<meio>(?:\s+[a-z0-9]+){{0,3}}?)\s+(?:temos|tem|possuimos|{_HA})"
    r"(?P<cauda>(?:\s+(?:sim|nao|mais|ainda|agora|hoje|no\s+momento|estoque|em\s+estoque"
    r"|com\s+estoque|disponive(?:l|is)|a\s+pronta\s+entrega|pronta\s+entrega))*)\s*(?:,|$)"
)
# …ou o estado ("está disponível", "estão esgotados", "esgotou"; "o laranja
# esgotado" sem o verbo também).
_ESTADO_DEPOIS = re.compile(
    r"(?P<meio>(?:\s+[a-z0-9]+){0,2}?)\s+"
    r"(?P<copula>(?:esta|estao|ta|tao|fica|ficam|segue|seguem|continua|continuam)\s+"
    r"(?:(?:sim|nao|ainda|tambem|mais|agora|hoje|no\s+momento)\s+)*)?"
    r"(?:(?P<pos>disponive(?:l|is)|em\s+estoque|com\s+estoque|a\s+pronta\s+entrega)"
    r"|(?P<neg>esgotad[oa]s?|esgot(?:ou|aram)|indisponive(?:l|is)|acab(?:ou|aram)(?!\s+de\b)"
    r"|em\s+falta|sem\s+estoque|fora\s+de\s+estoque|zerad[oa]s?))\b"
)
# …ou a existência negada ("a cor bordô não aparece entre as opções"; a
# afirmada, "o branco aparece no anúncio", não fala de estoque).
_NAO_EXISTE_DEPOIS = re.compile(
    r"(?P<meio>(?:\s+[a-z0-9]+){0,2}?)\s+(?:aparecem?|constam?|existem?)\b"
)
# "Confira se o branco está disponível": condição, não afirmação.
_SE_ANTES = re.compile(r"\bse\s+(?:[a-z0-9]+\s+){0,3}$")
# Ressalva, não falta de estoque: "não temos como confirmar", "sem previsão".
_RESSALVA = re.compile(
    r"\bnao\s+(?:temos|tenho|tem|ha)\s+(?:(?:a|uma|essa|esta)\s+)?(?:como|informac\w*|dados?"
    r"|certeza|previsao|quantidade|data|confirmac\w*"
    # Eval real de 21/10/2026 (dev a6ff4d22739b): "não temos uma comparação de
    # medidas entre os dois" não é estoque (era o "não temos" do 46mm).
    r"|comparac\w*|detalhes?|acesso|opiniao|indicac\w*|recomendac\w*)\b"
)
# Disponibilidade da EQUIPE ("estamos à disposição", "fico disponível").
_DISPONIVEL_A_EQUIPE = re.compile(
    r"\b(?:estamos|estou|fico|ficamos|seguimos|sigo|continuamos|permanecemos)\s+"
    r"(?:a\s+)?(?:sua\s+)?(?:inteira\s+)?(?:disposicao|disponive(?:l|is))\b"
)
# "Confira as opções disponíveis no anúncio": orientação, não afirmação.
_ORIENTA_ANUNCIO = re.compile(
    r"\b(?:anuncio|selecao|selecionar)\b.*\b(?:confir|verifi|consult|vej|aparec|selec|escolh)\w*"
    r"|\b(?:confir|verifi|consult|vej|aparec|selec|escolh)\w*\b.*\b(?:anuncio|selecao)\b"
)
# "Está disponível" sem a opção: só com o verbo perto ("está", "ainda",
# "temos", "há unidades") e sem um dado do pedido antes ("os dados
# disponíveis do pedido", "a previsão está disponível no pedido" não falam de
# estoque).
_VERBO_DO_DISPONIVEL = re.compile(
    r"\b(?:esta|estao|ta|fica|ficam|segue|seguem|continua|continuam|ainda|temos|tem|ha|sim)"
    r"\s+(?:[a-z0-9]+\s+){0,2}$"
)
_DADO_DO_PEDIDO = re.compile(
    r"\b(?:dados?|informac\w+|previsao|rastreio|codigo|nota|nf|link|prazo|atendimento|equipe"
    r"|opcao|opcoes|pedido|envio|entrega|pagamento|boleto|pix)\b"
)
# "Temos sim!", "Infelizmente não temos.", "Tem, sim", "Não há.": a opção fica
# implícita. "Temos todas as cores" também.
_TODAS = re.compile(
    r"\b(?:temos|tem|ha|vem|trabalhamos\s+com)\s+(?:em\s+|com\s+)?(?:tod[oa]s|qualquer)"
    r"\s+(?:as\s+|os\s+)?(?:cor|cores|opc\w+|variac\w+|modelos?|versoes|tamanhos?)\b"
)
# A resposta que começa com "Sim" / "Claro" responde à pergunta do cliente.
_SIM_NO_INICIO = re.compile(r"^\W*(?:ola\W+)?(?:sim|claro|com\s+certeza)\b")
# O cliente fala do pedido JÁ FEITO ("eu comprei a preta né?", "no meu pedido
# veio a azul?"): o "Sim" do começo confirma o pedido, não o estoque (ajuste
# r3 de 20/10/2026).
_PEDIDO_FEITO = re.compile(
    r"\b(?:comprei|compramos|pedi|recebi|chegou|veio|vieram|paguei|meu\s+pedido|minha\s+compra"
    r"|no\s+(?:meu|seu)\s+pedido|o\s+que\s+(?:eu\s+)?comprei)\b"
)
# Ajuste rf3 (21/10/2026): com o PEDIDO na conversa, a pergunta do envio que
# cita a cor ("a preta já foi enviada?", "a mala preta já saiu?", "minha mala
# preta tá a caminho?"): o "Sim" responde o envio, não o estoque. Antes da
# compra (sem pedido), o "Sim" continua sendo estoque.
_PEDIDO_A_CAMINHO = re.compile(
    r"\b(?:enviad\w*|saiu|sairam|postad\w*|despachad\w*|a\s+caminho|cheg\w*|rastrei\w*"
    r"|em\s+transito)\b"
)
# Rodada 4 (achado 16 da re-revisão final): com o PEDIDO na conversa, a resposta
# que CONFIRMA o item do pedido ("Sim, o seu pedido é da Mala de bordo ABS 10kg
# preta", "a sua compra foi a cor azul") não fala de estoque.
_CONFIRMA_O_PEDIDO = re.compile(
    r"\b(?:o\s+seu|seu|o|a\s+sua|sua)\s+(?:pedido|compra)\s+(?:\w+\s+)?(?:e|eh|foi|veio|tem|contem"
    r"|consta\s+como)\s+(?:d[ao]s?|com|a|o|na|no)?\b"
)
_SO_O_VERBO = re.compile(
    r"\b(?:temos|tem|ha)\b(?:\s+(?:sim|nao|mais|ainda|no\s+momento|disponive\w*|em\s+estoque))*"
    r"\s*(?:,|$)"
)
_INTENCAO_ESTOQUE = re.compile(
    r"\b(?:estoque|disponive\w*|disponibilidade|esgot\w*|acab\w*|ainda\s+(?:tem|ha)"
    r"|tem\s+ainda|cor|cores|variac\w*|opc(?:ao|oes)|repos\w*|repor)\b"
)
_PROMESSA = re.compile(
    r"\b(?:ultimas?|poucas?)\s+unidades?\b|\breserv(?:amos|ar|ado|ada|aremos)\b"
    # Ajuste r3: "Esse é o último em estoque", "a última disponível".
    r"|\bultim[oa]s?\s+(?:em\s+estoque|disponive\w*|pecas?|unidades?|do\s+estoque)\b"
    r"|\bseparamos\b|\brepo(?:r|remos|sicao)\b|\bvolta(?:r|ra|rao)?\s+(?:ao|para\s+o)\s+estoque\b"
    r"|\bchega(?:r|ra|rao|m)?\s+(?:mais|nov[oa]s?)\b"
)
_JANELA = 60
_NEGA_PERTO = re.compile(r"\b(?:nao|nem|sem)\b")
# A volta ao estoque como CONDIÇÃO (Kia ao vivo, 21/10/2026, rascunho 1d6f708e:
# "Quando essa opção voltar ao estoque, ela aparece de novo para seleção no
# próprio anúncio" não promete a volta): a reposição dentro da oração de
# "quando/se/caso/assim que…" é a hipótese; a promessa estaria na oração
# PRINCIPAL, e só quando ela diz o que a LOJA faz (verbo na 1ª pessoa —
# "avisamos", "te aviso", "vamos separar" — ou a loja/equipe como sujeito).
# "Ela aparece de novo no anúncio" fala do anúncio, não da loja.
_REPOSICAO = re.compile(
    r"\brepo(?:r|remos|sicao)\b|\bvolta(?:r|ra|rao)?\s+(?:ao|para\s+o)\s+estoque\b"
    r"|\bchega(?:r|ra|rao|m)?\s+(?:mais|nov[oa]s?)\b"
)
_CONDICAO_ANTES = re.compile(
    r"\b(?:quando|se|caso|assim\s+que|logo\s+que|sempre\s+que|depois\s+que)\s+(?:[a-z0-9]+\s+){0,5}$"
)
_FIM_DA_ORACAO = re.compile(r"[.!?;\n]")
_ACAO_DA_LOJA = re.compile(
    r"\b(?:[a-z]+(?:amos|emos|imos)|vou|irei|(?:te|lhe|o|a)\s+(?:aviso|avisamos|chamo|chamamos"
    r"|notifico|notificamos|informo|informamos|mando|mandamos|envio|enviamos)"
    r"|(?:a|nossa)\s+(?:loja|equipe))\b"
)


def _reposicao_so_como_condicao(texto: str, m: re.Match[str]) -> bool:
    """A reposição de `m` é só a condição ("quando voltar ao estoque, aparece no
    anúncio") e a oração principal não diz o que a loja faz? PURA (só o agente
    lê: `conferir_variacoes` é das checagens dele)."""
    if not _REPOSICAO.fullmatch(m.group(0)):
        return False
    inicio = max((f.end() for f in _FIM_DA_ORACAO.finditer(texto, 0, m.start())), default=0)
    if not _CONDICAO_ANTES.search(texto[inicio : m.start()]):
        return False
    fim = _FIM_DA_ORACAO.search(texto, m.end())
    principal = texto[m.end() : fim.start() if fim else len(texto)]
    return not _ACAO_DA_LOJA.search(principal)


def _chaves(nome: Any) -> list[str]:
    """As palavras de um nome, na forma comparável (cor = "cor:" + raiz; memória e
    voltagem = "mem:"/"volt:")."""
    saida: list[str] = []
    palavras = _TOKEN.findall(_normal(nome))
    for i, p in enumerate(palavras):
        if cor := _cor_em(palavras, i):
            saida.append(cor)
        elif opcao := _de_opcao(p):
            saida.extend(sorted(opcao))
        elif len(p) >= 2 and p not in _SEM_SENTIDO:
            saida.append(p)
    return saida


def _lista_da_ficha(f: Any) -> _Lista | None:
    if not isinstance(f, Mapping) or not isinstance(f.get("variacoes"), list):
        return None
    opcoes = []
    for v in f["variacoes"]:
        if not isinstance(v, Mapping) or not _texto(v.get("nome")):
            continue
        disponivel = v.get("disponivel")
        codigos = frozenset(
            c.lower() for k in ("sku", "variation_id") if (c := _texto(v.get(k))) and len(c) >= 4
        )
        opcoes.append(
            _Opcao(
                nome=str(v["nome"]),
                chaves=frozenset(_chaves(v["nome"])),
                disponivel=disponivel if isinstance(disponivel, bool) else None,
                confiavel=v.get("confiavel") is True,
                codigos=codigos,
            )
        )
    if not opcoes:
        return None
    atributos = f.get("atributos") if isinstance(f.get("atributos"), list) else []
    fixo = " ".join([str(f.get("titulo") or ""), *(a for a in atributos if isinstance(a, str))])
    return _Lista(
        opcoes,
        f.get("variacoes_completas") is True,
        _tipos(c for o in opcoes for c in o.chaves),
        frozenset(c for c in _chaves(fixo) if ":" in c),
    )


def _listas(fatos: Mapping[str, Any]) -> list[_Lista | None]:
    """A lista de cada ficha, na ordem das fichas (None = ficha sem variações)."""
    fichas = fatos.get("ficha_do_anuncio") if isinstance(fatos, Mapping) else None
    return [_lista_da_ficha(f) for f in fichas] if isinstance(fichas, list) else []


def _mencoes(texto: str, lista: _Lista | None) -> list[_Mencao]:
    """As citações de variação, cor, memória ou voltagem no texto (já na forma `_normal`).

    Palavras vizinhas que juntas são de UMA variação ("o preto de 128gb")
    formam uma citação; "branco e laranja", duas. Palavra que não é cor nem
    memória só conta quando distingue uma variação de outra (o "nfc" de
    todas não); o número solto ("o de 256") só quando o anúncio varia de
    memória.
    """
    opcoes = lista.opcoes if lista else []
    distintivas: set[str] = set()
    if opcoes:
        comuns = frozenset.intersection(*(o.chaves for o in opcoes))
        for o in opcoes:
            distintivas |= o.chaves - comuns
    memoria_solta = lista is not None and "mem" in lista.tipos
    palavras = list(_TOKEN.finditer(texto))
    so_palavras = [m.group() for m in palavras]
    chaves: list[frozenset[str]] = []
    for i, p in enumerate(so_palavras):
        if cor := _cor_em(so_palavras, i):
            chaves.append(frozenset({cor}))
        elif opcao := _de_opcao(p):
            chaves.append(opcao)
        elif p in distintivas:
            chaves.append(frozenset({p}))
        elif memoria_solta and p in _MEMORIA_SOLTA:
            chaves.append(frozenset({f"mem:{p}"}))
        else:
            chaves.append(frozenset())
    mencoes: list[_Mencao] = []
    atual: list[int] = []

    def _fechar() -> None:
        if atual:
            inicio, fim = palavras[atual[0]].start(), palavras[atual[-1]].end()
            juntas = frozenset().union(*(chaves[i] for i in atual))
            mencoes.append(_Mencao(inicio, fim, juntas, texto[inicio:fim]))

    for i, chave in enumerate(chaves):
        if not chave:
            continue
        if atual:
            entre = {palavras[k].group() for k in range(atual[-1] + 1, i)}
            junto = frozenset().union(*(chaves[k] for k in atual)) | chave
            if (
                i - atual[-1] <= 3
                and not entre & _CONJUNCAO
                and "," not in texto[palavras[atual[-1]].end() : palavras[i].start()]
                and any(junto <= o.chaves for o in opcoes)
            ):
                atual.append(i)
                continue
        _fechar()
        atual = [i]
    _fechar()
    return mencoes


def _depois_da_virgula(antes: str, da_lista: frozenset[str]) -> str:
    """O trecho de `antes` que vale: a vírgula só passa quando o resto é lista."""
    for i in range(len(antes) - 1, -1, -1):
        if antes[i] == "," and not set(_PALAVRA.findall(antes[i + 1 :])) <= da_lista:
            return antes[i + 1 :]
    return antes


def _predicado_depois(resto: str) -> str | None:
    """O predicado colado depois da citação: "+", "-", "+fraco" ("o branco
    disponível" sem verbo: vale só sem marca antes) ou None."""
    if m := _TER_DEPOIS.match(resto):
        return "-" if _NEGA.search(m.group("meio") + " " + m.group("cauda")) else "+"
    if m := _ESTADO_DEPOIS.match(resto):
        if m.group("neg"):
            return "-"
        if _NEGA.search(m.group("meio") + " " + (m.group("copula") or "")):
            return "-"
        return "+" if m.group("copula") else "+fraco"
    if (m := _NAO_EXISTE_DEPOIS.match(resto)) and _NEGA.search(m.group("meio")):
        return "-"
    return None


# O parêntese logo depois da citação: "a Preto (disponível)".
_PARENTESE_COLADO = re.compile(r"\s*\(([^()]*)")
_PARENTESE_FECHADO = re.compile(r"\([^()]*\)")


def _depois_da_citacao(sentenca: str, mencao: _Mencao, ate: int) -> str:
    """O trecho depois da citação em que vale a marca de estoque mais adiante.

    O parêntese que cita OUTRA variação é outra oração (eval real de
    21/10/2026, dev a6ff4d22739b: "em 46mm, a cor Prata (Preto 42mm e Cinza
    46mm estão esgotados)" lia o "esgotados" de dentro como o da Prata): o
    trecho para no "(" quando a próxima citação (`ate`) começa dentro dele, ou
    quando o parêntese vem depois dela. O parêntese logo depois da citação é
    dela ("a Preto (disponível)", "a Prata (esgotada)", "temos a Prata (no
    momento está esgotada)": revisão r5i de 21/10/2026). A citação de dentro
    de um parêntese ("(Preto 42mm e Cinza 46mm estão esgotados)") para no ")".
    """
    fim = min(len(sentenca), mencao.fim + _JANELA)
    abertos: list[int] = []
    for i, c in enumerate(sentenca[: mencao.fim]):
        if c == "(":
            abertos.append(i)
        elif c == ")" and abertos:
            abertos.pop()
    # O "(" ainda aberto que começou antes da citação: ela está dentro dele.
    dentro = bool(abertos) and abertos[-1] < mencao.inicio
    j = mencao.fim
    while j < fim:
        c = sentenca[j]
        if c == ")" and dentro:
            return sentenca[mencao.fim : j]
        if c == "(":
            fecha = sentenca.find(")", j)
            fecha = len(sentenca) if fecha < 0 else fecha
            if ate < len(sentenca) and ate <= fecha:
                # A próxima citação vem antes do parêntese ou dentro dele.
                return sentenca[mencao.fim : j]
            j = fecha
        j += 1
    return sentenca[mencao.fim : fim]


def _polaridade(sentenca: str, mencao: _Mencao, da_lista: frozenset[str], ate: int) -> str | None:
    """ "+" (tem/disponível), "-" (não tem/esgotado) ou None (só citou).

    Nesta ordem: condição ("confira se o branco…") não afirma; o predicado
    colado DEPOIS da citação, até a próxima ("o laranja não temos mais", "o
    branco está disponível e o laranja esgotado"); a marca mais próxima
    ANTES dela ("não temos na cor bordô"; "apenas"/"só" só colado nela: "só
    o branco"), sem passar de vírgula que não seja de lista ("não temos o
    bordô, as opções são o branco…"); o adjetivo colado ("temos o branco
    disponível"); a marca de estoque mais adiante ("o branco e o laranja,
    os dois disponíveis").
    """
    if _SE_ANTES.search(sentenca[: mencao.inicio]):
        return None
    colado = _predicado_depois(sentenca[mencao.fim : ate])
    if colado in ("+", "-"):
        return colado
    # O parêntese colado é da citação e vale antes da marca de antes ("temos a
    # Prata (no momento está esgotada)", "temos a Prata e a Preto (esgotada)":
    # revisão r5i de 21/10/2026) — se a próxima citação não começa dentro dele.
    if (parentese := _PARENTESE_COLADO.match(sentenca, mencao.fim)) and ate >= parentese.end(1):
        dentro = parentese.group(1)
        for marca in _MARCA.finditer(dentro):
            if marca.lastgroup in ("neg", "pos"):
                negada = bool(_NEGA.search(dentro[: marca.start()]))
                return "-" if (marca.lastgroup == "neg") != negada else "+"
    # O parêntese fechado antes da citação é da citação de antes dele ("a Preto
    # (não está disponível) e a Prata sim"): sai da janela, com o tamanho.
    antes = _depois_da_virgula(
        _PARENTESE_FECHADO.sub(
            lambda m: " " * len(m.group()),
            sentenca[max(0, mencao.inicio - _JANELA) : mencao.inicio],
        ),
        da_lista,
    )
    for marca in reversed(list(_MARCA.finditer(antes))):
        if marca.lastgroup == "so":
            if len(_PALAVRA.findall(antes[marca.end() :])) <= 2:
                return "+"
            continue
        if marca.lastgroup == "neg":
            return "-"
        return "-" if _NEGA_ANTES.search(antes[: marca.start()]) else "+"
    if colado == "+fraco":
        return "+"
    depois = _depois_da_citacao(sentenca, mencao, ate)
    for marca in _MARCA.finditer(depois):
        if marca.lastgroup == "neg":
            return "-"
        if marca.lastgroup == "pos":
            return "-" if _NEGA.search(depois[: marca.start()]) else "+"
    return None


def _sentencas(texto: str) -> list[tuple[str, bool]]:
    """(sentença, é pergunta) — "mas"/"porém" abrem outra sentença; o ponto
    entre números ("8.128") não fecha."""
    saida: list[tuple[str, bool]] = []
    inicio = 0
    for sep in [*_SENTENCA.finditer(texto), None]:
        fim = sep.start() if sep else len(texto)
        trecho = texto[inicio:fim].strip(" ,:")
        if trecho:
            saida.append((trecho, bool(sep and "?" in sep.group())))
        inicio = sep.end() if sep else len(texto)
    return saida


# De QUE a sentença fala (Kia ao vivo, 21/10/2026, rascunho 8d62a0e7: "Neste
# anúncio não há capa à venda" virava "não tem 'laranja'" — o cliente pedira a
# capa laranja e o "não há" era da CAPA, não da cor do aparelho). O "temos/há"
# e o "está esgotado/disponível" sem a opção só valem para a opção do cliente
# quando o que eles têm por objeto (ou por sujeito) é a própria opção: nada
# ("Temos sim!", "Está esgotado."), um nome genérico dela ("não temos essa
# cor", "o produto está esgotado", "não há estoque"), uma palavra da lista, uma
# cor, uma memória ou uma palavra do título do anúncio. Outra coisa ("capa",
# "carregador avulso") é o assunto da sentença: ela não fala da opção.
_OBJETO_DA_OPCAO = frozenset(
    """
    estoque estoques unidade unidades opcao opcoes variacao variacoes cor cores modelo modelos
    versao versoes tamanho tamanhos produto produtos item itens peca pecas aparelho aparelhos
    disponivel disponiveis disponibilidade anuncio anuncios tudo todo toda todos todas pronta
    """.split()
)
_DETERMINANTE = frozenset(
    """
    o a os as um uma uns umas esse essa esses essas este esta estes estas seu sua seus suas
    """.split()
)
_SEM_PESO_NO_OBJETO = frozenset(
    """
    o a os as um uma uns umas esse essa esses essas este esta estes estas isso isto aquele aquela
    ele ela eles elas mais ainda sim nao nem no na nos nas momento agora hoje mesmo mesma tambem
    infelizmente de do da dos das em la aqui ai nenhum nenhuma algum alguma qualquer outro outra
    outros outras so apenas somente ja que e esta estao ta fica ficam segue seguem continua
    continuam seu sua seus suas nosso nossa
    """.split()
)


def _fala_de_outra_coisa(
    palavras: Sequence[str], conhecidas: frozenset[str], *, sujeito: bool = False
) -> bool:
    """A 1ª palavra de conteúdo de `palavras` nomeia outra coisa que não a opção?

    `sujeito` = `palavras` vêm de trás para a frente, antes do "está
    esgotado/disponível": só o nome com artigo ou demonstrativo antes ("a capa
    está esgotada") é o sujeito — um verbo ("ficou esgotado") não é.
    """
    for i, p in enumerate(palavras):
        if p in _SEM_PESO_NO_OBJETO:
            continue
        if sujeito and not (i + 1 < len(palavras) and palavras[i + 1] in _DETERMINANTE):
            return False
        return not (
            p in _OBJETO_DA_OPCAO
            or p in conhecidas
            or p in _CORES
            or p.isdigit()
            or bool(_de_opcao(p))
        )
    return False


def _implicita(sentenca: str, conhecidas: frozenset[str] = frozenset()) -> str | None:
    """A sentença afirma estoque sem citar a opção? ("Temos sim!", "Está esgotado.")

    `conhecidas` = as palavras das opções e do título do anúncio: com elas, o
    objeto do verbo de ter (ou o sujeito do "está esgotado") que nomeia OUTRA
    coisa ("não há capa à venda") tira a sentença da conta (ver acima).
    """
    if _ORIENTA_ANUNCIO.search(sentenca):
        return None
    if todas := _TODAS.search(sentenca):
        return "-" if _NEGA_ANTES.search(sentenca[: todas.start()]) else "+"
    for marca in _MARCA.finditer(sentenca):
        antes = sentenca[: marca.start()]
        negada = bool(_NEGA_ANTES.search(antes))
        if conhecidas and (
            (
                marca.lastgroup == "verbo"
                and _fala_de_outra_coisa(_TOKEN.findall(sentenca[marca.end() :])[:4], conhecidas)
            )
            or (
                marca.lastgroup in ("neg", "pos")
                and _fala_de_outra_coisa(_TOKEN.findall(antes)[::-1][:5], conhecidas, sujeito=True)
            )
        ):
            continue
        if marca.lastgroup == "neg" or (marca.lastgroup == "verbo" and negada):
            return "-"
        if marca.lastgroup == "pos" and not _DADO_DO_PEDIDO.search(antes):
            if negada:
                return "-"
            if _VERBO_DO_DISPONIVEL.search(antes) or not antes.strip():
                return "+"
    so_verbo = _SO_O_VERBO.search(sentenca)
    if so_verbo:
        trecho = sentenca[: so_verbo.end()]
        return "-" if _NEGA.search(trecho) else "+"
    return None


def _titulo_principal(fatos: Mapping[str, Any]) -> str:
    """O título da 1ª ficha (a do anúncio de que o cliente fala), ou ""."""
    fichas = fatos.get("ficha_do_anuncio") if isinstance(fatos, Mapping) else None
    if isinstance(fichas, list) and fichas and isinstance(fichas[0], Mapping):
        titulo = fichas[0].get("titulo")
        return titulo if isinstance(titulo, str) else ""
    return ""


def _promete_estoque(texto: str) -> bool:
    """ "Últimas unidades", "reservamos", "vamos verificar a reposição" — e não
    "não temos data de reposição" (negada nas palavras de antes: produção,
    20/10/2026, 4 de 5 rascunhos que citavam reposição eram assim), nem a
    volta ao estoque que é só a condição da oração (Kia ao vivo, 21/10/2026:
    "quando essa opção voltar ao estoque, ela aparece de novo no anúncio")."""
    for m in _PROMESSA.finditer(texto):
        if _NEGA_PERTO.search(texto[max(0, m.start() - 45) : m.start()]):
            continue
        if _reposicao_so_como_condicao(texto, m):
            continue
        return True
    return False


def _rotulo(mencao: _Mencao, opcoes: list[_Opcao]) -> str:
    return opcoes[0].nome if len(opcoes) == 1 else mencao.texto


def conferir_variacoes(
    resposta: str | None,
    fatos: Mapping[str, Any],
    textos_cliente: Iterable[str | None] = (),
    *,
    estoque_aprovado: bool = False,
    com_pedido: bool = False,
) -> list[Pendencia]:
    """O que a resposta afirma de variação e estoque contra o dado da ficha. PURA.

    Ver o topo. `fatos["ficha_do_anuncio"]` = as fichas com as variações
    (`na_ficha`, a do agente; com o sku e o variation_id do REGISTRO, a
    conferência também pega o código interno citado no texto).
    `estoque_aprovado` = o caminho pode afirmar disponível/esgotado (o
    agente, decisão 2 do dono de 20/10/2026); sem ele, toda afirmação de
    estoque vai para pessoa. Pergunta ("quer o branco?") e orientação
    ("confira no anúncio") não afirmam nada. Sem nenhuma afirmação, [].
    `com_pedido` (ajuste rf3) = há pedido na conversa: a pergunta do envio que
    cita a cor ("a preta já foi enviada?") não pede estoque.
    """
    if not resposta:
        return []
    fatos = fatos if isinstance(fatos, Mapping) else {}
    listas = _listas(fatos)
    # A lista do anúncio de que o cliente fala: a da 1ª ficha (a mais recente).
    principal = listas[0] if listas else None
    outras_com_variacoes = any(lista is not None for lista in listas[1:])
    opcoes = principal.opcoes if principal else []
    tem_cor = principal is not None and "cor" in principal.tipos
    cliente = _normal(" ".join(t for t in textos_cliente if isinstance(t, str)))
    do_cliente = _mencoes(cliente, principal)
    cores_cliente = {c for m in do_cliente for c in m.chaves if c.startswith("cor:")}
    cores_nos_fatos = {
        c for c in _chaves(json.dumps(fatos, ensure_ascii=False)) if c.startswith("cor:")
    }
    intencao = bool(do_cliente) or bool(_INTENCAO_ESTOQUE.search(cliente))
    da_lista = (
        _DA_LISTA
        | frozenset(p for o in opcoes for p in _PALAVRA.findall(_normal(o.nome)))
        | frozenset(_CORES)
    )
    # O que a sentença sem a opção pode ter por objeto e ainda falar dela (as
    # palavras da lista e do título do anúncio; ver `_implicita`).
    conhecidas = da_lista | frozenset(_PALAVRA.findall(_normal(_titulo_principal(fatos))))
    sem_equipe = _DISPONIVEL_A_EQUIPE.sub(" ", _normal(resposta))
    # A ressalva sai da conta das afirmações, não da da promessa ("não temos
    # data de reposição" é a negação que livra a reposição).
    texto = _RESSALVA.sub(" ", sem_equipe)

    pendencias: dict[str, Pendencia] = {}
    afirmou = False
    # As chaves das citações já conferidas (o "Sim" do começo vale para as
    # do cliente que nenhuma frase conferiu).
    conferidas: list[frozenset[str]] = []
    # False = a afirmação é deduzida (o "Sim" do começo): no máximo pessoa.
    pode_bloquear = True

    def _anotar(motivo: str, bloqueia: bool) -> None:
        anterior = pendencias.get(motivo)
        bloqueia = bloqueia and pode_bloquear
        pendencias[motivo] = Pendencia(motivo, bloqueia or bool(anterior and anterior.bloqueia))

    def _fora_da_lista(principal: _Lista, mencao: _Mencao, sinal: str) -> None:
        """A opção citada não é nenhuma das variações."""
        tipos = _tipos(mencao.chaves)
        if tipos and tipos <= principal.tipos:
            # Do mesmo tipo das variações: a lista completa prova que não existe.
            if sinal == "+":
                if principal.completa:
                    _anotar(f"diz que tem '{mencao.texto}', que não está nas variações", True)
                else:
                    _anotar(f"diz que tem '{mencao.texto}' sem a lista completa", False)
            elif not principal.completa:
                _anotar(f"diz que não tem '{mencao.texto}' sem a lista completa", False)
        elif mencao.chaves <= principal.fixas:
            # Fixa no anúncio (título, atributos): fala do anúncio inteiro.
            if sinal == "+":
                if not any(o.disponivel and o.confiavel for o in opcoes):
                    _anotar(
                        f"diz que tem '{mencao.texto}' sem variação disponível confiável", False
                    )
            elif not all(o.disponivel is False and o.confiavel for o in opcoes):
                _anotar(f"diz que não tem '{mencao.texto}', que está no título do anúncio", True)
        else:
            verbo = "tem" if sinal == "+" else "não tem"
            _anotar(f"diz que {verbo} '{mencao.texto}', que as variações não cobrem", False)

    def _conferir(mencao: _Mencao, sinal: str) -> None:
        nonlocal afirmou
        afirmou = True
        conferidas.append(mencao.chaves)
        if principal is None:
            _anotar("disponibilidade de variação sem dado de estoque na ficha", False)
            return
        casadas = [o for o in opcoes if mencao.chaves <= o.chaves]
        if not casadas:
            _fora_da_lista(principal, mencao, sinal)
            return
        rotulo = _rotulo(mencao, casadas)
        if sinal == "+":
            if any(o.disponivel and o.confiavel for o in casadas):
                return
            if all(o.disponivel is False and o.confiavel for o in casadas):
                _anotar(f"diz que tem '{rotulo}', que está esgotada", True)
            else:
                _anotar(f"diz que tem '{rotulo}' sem estoque confiável", False)
        else:
            if all(o.disponivel is False and o.confiavel for o in casadas):
                return
            if any(o.disponivel and o.confiavel for o in casadas):
                _anotar(f"diz que não tem '{rotulo}', que está disponível", True)
            else:
                _anotar(f"diz que não tem '{rotulo}' sem estoque confiável", False)

    implicitas: list[str] = []
    # Citadas sem dizer se tem ("A cor bordô é linda."): o "Temos sim!" de
    # outra frase vale para elas quando o cliente não citou nenhuma.
    neutras: list[_Mencao] = []
    for sentenca, pergunta in _sentencas(texto):
        if pergunta:
            continue
        mencoes = _mencoes(sentenca, principal)
        if not mencoes:
            if sinal := _implicita(sentenca, conhecidas):
                implicitas.append(sinal)
            continue
        for k, mencao in enumerate(mencoes):
            ate = mencoes[k + 1].inicio if k + 1 < len(mencoes) else len(sentenca)
            sinal = _polaridade(sentenca, mencao, da_lista, ate)
            if sinal is not None:
                _conferir(mencao, sinal)
                continue
            neutras.append(mencao)
            desconhecidas = {
                c
                for c in mencao.chaves
                if c.startswith("cor:") and not any(c in o.chaves for o in opcoes)
            }
            if tem_cor and desconhecidas - cores_cliente - cores_nos_fatos:
                _anotar(f"cita a cor '{mencao.texto}', que não está nas variações", False)
    # Revisão de 20/10/2026: o "Temos sim!" vale mesmo que OUTRA frase cite
    # uma cor ("Temos sim! A cor bordô é linda." passava).
    if implicitas and intencao:
        for sinal in dict.fromkeys(implicitas):
            if do_cliente:
                for mencao in do_cliente:
                    _conferir(mencao, sinal)
                continue
            if sinal == "+" and neutras:
                for mencao in neutras:
                    _conferir(mencao, sinal)
                continue
            afirmou = True
            if not opcoes:
                _anotar("disponibilidade sem dado de estoque na ficha", False)
            elif sinal == "+" and not any(o.disponivel and o.confiavel for o in opcoes):
                _anotar("diz que tem sem variação disponível confiável", False)
            elif sinal == "-" and not all(o.disponivel is False and o.confiavel for o in opcoes):
                _anotar("diz que acabou sem todas as variações esgotadas", False)
    # "Sim! Pode comprar." para "tem o bordô?": a resposta começa dizendo que
    # tem a opção que o cliente citou e nenhuma frase a conferiu.
    if (
        intencao
        and _SIM_NO_INICIO.search(texto)
        and not _PEDIDO_FEITO.search(cliente)
        and not (com_pedido and _PEDIDO_A_CAMINHO.search(cliente))
        and not (com_pedido and _CONFIRMA_O_PEDIDO.search(texto))
    ):
        pode_bloquear = False
        for mencao in do_cliente:
            if not any(c & mencao.chaves for c in conferidas):
                _conferir(mencao, "+")
        pode_bloquear = True
    if afirmou and not estoque_aprovado:
        _anotar(MOTIVO_ESTOQUE_SEM_REGRA, False)
    if afirmou and outras_com_variacoes:
        _anotar(MOTIVO_DUAS_FICHAS, False)
    if _promete_estoque(sem_equipe):
        _anotar("promessa de estoque (últimas unidades, reserva ou reposição)", False)
    minusculo = resposta.lower()
    codigos = {c for lista in listas if lista for o in lista.opcoes for c in o.codigos}
    if any(re.search(rf"(?<![\w.+-]){re.escape(c)}(?![\w.+-])", minusculo) for c in codigos):
        _anotar("cita o sku ou o id interno da variação", False)
    return list(pendencias.values())
