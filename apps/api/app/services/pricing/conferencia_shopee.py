"""Leitor do preço VIVO da Shopee para a Conferência de preços da Tabela
(02/11/2026). Interface e regra de ouro em `conferencia_leitores`.

O que o envio grava (`ShopeeClient.update_price`): NUNCA o preço base — o
preço promocional do "Desconto de Loja" (`update`/`add_discount_item`,
arredondado em reais inteiros). Sem desconto ativo o envio dá erro; anúncio só
em Oferta Relâmpago é pulado. Então o comparável é o `current_price` (o preço
COM o desconto): do `get_item_base_info` no anúncio sem variação (lotes de 50)
e do `get_model_list` na variação.

Mas o `current_price` só é o do Desconto de Loja quando é o desconto que está
valendo. Preço abaixo do original abre as promoções do anúncio
(`get_item_promotion`, em lotes de 50):
  - Desconto de Loja e nada por cima → confere;
  - Oferta Relâmpago ou campanha por cima (o preço da vitrine é DELAS) → não
    confere, e o preço da vitrine vai em `preco_promocional`;
  - promoção que não se reconhece → não confere.
Sem promoção nenhuma (current == original) confere o preço base, com o motivo
"sem desconto ativo": ali o envio daria erro até alguém criar o desconto.

SÓ LEITURA. Uma loja por chamada, e o núcleo lê uma loja Shopee por vez
(`conferencia.UMA_LOJA_POR_VEZ`): o limite de chamadas da Shopee é por
partner-id, dividido com o resto do sistema. A recusa por limite (HTTP 429,
error_busy, too many…) espera 1, 2 e 4 s e tenta de novo; entre um
`get_model_list` e o próximo há uma folga, como na importação.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

import httpx
import structlog

from app.models import ProductLink
from app.services.pricing.conferencia_leitores import LeituraAnuncio, dinheiro, registrar
from app.services.troca_sku_anuncio import eh_limite

logger = structlog.get_logger()

# Teto de anúncios por chamada do get_item_base_info e do get_item_promotion.
LOTE_SHOPEE = 50
# Recusa por limite de chamadas: espera e tenta de novo (como o leitor da Amazon).
ESPERAS_LIMITE_S = (1.0, 2.0, 4.0)
HTTP_LIMITE = 429
# O `_call` do cliente põe o status no texto do erro quando a resposta não é JSON.
_HTTP_429 = re.compile(rf"\bHTTP {HTTP_LIMITE}\b")
# Folga entre um get_model_list e o próximo (a importação dorme 0,5 s).
INTERVALO_MODELOS_S = 0.4

# O Desconto de Loja no `promotion_type` — os mesmos nomes que o envio aceita
# (`ShopeeClient.get_item_promotion`).
_TIPOS_DESCONTO_LOJA = frozenset({"discount promotions", "discount", "shop_discount"})
# Promoções que põem o preço DELAS na vitrine por cima do desconto (trecho do
# `promotion_type` → motivo). "flash" é o mesmo teste do envio; campanha é a
# "Campaign"/`platform_sale` (10.10, 11.11) e a Brand Sale.
_POR_CIMA_DO_DESCONTO = (
    ("flash", "em Oferta Relâmpago — não conferido"),
    ("campaign", "em campanha da Shopee — não conferido"),
    ("platform_sale", "em campanha da Shopee — não conferido"),
    ("brand", "em campanha da Shopee — não conferido"),
)
# Promoções que não mexem no preço do anúncio (valem no carrinho ou para um
# comprador só): leve mais, compre junto, atacado, brinde, boas-vindas, preço
# exclusivo, compra em grupo.
_NAO_MEXEM_NO_PRECO = (
    "bundle",
    "add_on",
    "add-on",
    "whole",
    "gift",
    "welcome",
    "exclusive",
    "group",
)
# `promotion_staging` da promoção agendada (ainda não vale).
_ETAPA_AGENDADA = "upcoming"

_STATUS_ATIVO = "NORMAL"
_STATUS_APAGADO = frozenset({"DELETED", "SELLER_DELETE", "SHOPEE_DELETE"})

# Erro da CONTA (token, permissão, chave do app): levanta, e a conferência
# marca a conta inteira. O resto é pontual e vira motivo do anúncio.
_MARCAS_ERRO_CONTA = (
    "error_auth",
    "error_token",
    "error_permission",
    "partner_key",
    "access_token",
    "refresh_token",
)
_TAMANHO_DO_ERRO = 120


@dataclass(frozen=True)
class _PrecoVivo:
    """O que a Shopee mostra para um vínculo, antes de olhar as promoções."""

    item_id: int
    modelo: int
    atual: Decimal
    original: Decimal | None
    ativo: bool | None


class _Folga:
    """Uma pausa antes de cada chamada, menos a primeira."""

    def __init__(self, segundos: float) -> None:
        self.segundos = segundos
        self.primeira = True

    async def esperar_a_vez(self) -> None:
        if not self.primeira:
            await _esperar(self.segundos)
        self.primeira = False


async def _esperar(segundos: float) -> None:
    await asyncio.sleep(segundos)


def _eh_limite(exc: BaseException) -> bool:
    """A Shopee recusou por limite de chamadas? O mesmo critério da troca de
    SKU (`troca_sku_anuncio.eh_limite`: 429, error_busy, too many, rate limit,
    error_rate)."""
    texto = str(exc)
    return eh_limite("shopee", HTTP_LIMITE if _HTTP_429.search(texto) else None, texto)


async def _com_novas_tentativas[T](chamada: Callable[..., Awaitable[T]], *args: Any) -> T:
    """A chamada à Shopee, de novo depois de 1, 2 e 4 s quando a recusa é por
    limite de chamadas. Outro erro — ou o limite que não passou — sobe."""
    for espera in ESPERAS_LIMITE_S:
        try:
            return await chamada(*args)
        except Exception as exc:  # noqa: BLE001 — só o limite tenta de novo
            if _erro_da_conta(exc) or not _eh_limite(exc):
                raise
            logger.info("conferencia_shopee_limite", espera_s=espera, erro=str(exc)[:200])
        await _esperar(espera)
    return await chamada(*args)


@registrar("shopee")
async def ler_precos_shopee(
    cliente: Any, vinculos: list[ProductLink]
) -> dict[UUID, LeituraAnuncio]:
    """Uma `LeituraAnuncio` por vínculo da loja (chave = `ProductLink.id`).
    `external_id` = item_id; `variation_id` = model_id ("0"/vazio = anúncio
    sem variação, como no envio)."""
    leituras: dict[UUID, LeituraAnuncio] = {}
    por_item: dict[int, list[ProductLink]] = {}
    for vinculo in vinculos:
        item_id = _inteiro(vinculo.external_id)
        if item_id is None:
            leituras[vinculo.id] = LeituraAnuncio(preco=None, motivo="ID do anúncio inválido")
        else:
            por_item.setdefault(item_id, []).append(vinculo)

    itens, falhas = await _ler_itens(cliente, list(por_item))
    vivos: dict[UUID, _PrecoVivo] = {}
    folga = _Folga(INTERVALO_MODELOS_S)
    for item_id, do_item in por_item.items():
        item = itens.get(item_id)
        if item is None:
            motivo = falhas.get(item_id, "anúncio não encontrado na Shopee")
            leituras.update({v.id: LeituraAnuncio(preco=None, motivo=motivo) for v in do_item})
            continue
        lidos, sem_preco = await _vivos_do_item(cliente, item_id, item, do_item, folga)
        vivos.update(lidos)
        leituras.update(sem_preco)

    # Só o preço abaixo do original (ou sem original) precisa saber QUAL
    # promoção está valendo.
    abaixo = sorted({p.item_id for p in vivos.values() if _abaixo_do_original(p)})
    promocoes, sem_promocoes = await _ler_promocoes(cliente, abaixo)
    agora = int(time.time())
    for link_id, vivo in vivos.items():
        leituras[link_id] = _conferir(vivo, promocoes, sem_promocoes, agora)
    return leituras


async def _ler_itens(cliente: Any, item_ids: list[int]) -> tuple[dict[int, dict], dict[int, str]]:
    """`get_item_base_info` em lotes de 50 → (itens lidos, motivo por item que
    falhou). Erro da conta levanta; NENHUM lote lido também levanta (a conta
    inteira ficou sem leitura)."""
    itens: dict[int, dict] = {}
    falhas: dict[int, str] = {}
    primeiro_erro: Exception | None = None
    for lote in _lotes(item_ids):
        try:
            lidos = await _com_novas_tentativas(cliente.get_item_base_info, lote)
        except Exception as exc:  # noqa: BLE001 — erro pontual vira motivo
            if _erro_da_conta(exc):
                raise
            logger.info("conferencia_shopee_lote_falhou", itens=len(lote), erro=str(exc)[:200])
            primeiro_erro = primeiro_erro or exc
            falhas.update(dict.fromkeys(lote, _motivo_falha("falha ao ler o anúncio", exc)))
            continue
        for item in lidos:
            item_id = _inteiro(item.get("item_id")) if isinstance(item, dict) else None
            if item_id is not None:
                itens[item_id] = item
    if primeiro_erro is not None and len(falhas) == len(item_ids):
        raise primeiro_erro
    return itens, falhas


async def _vivos_do_item(
    cliente: Any, item_id: int, item: dict, vinculos: list[ProductLink], folga: _Folga
) -> tuple[dict[UUID, _PrecoVivo], dict[UUID, LeituraAnuncio]]:
    """(preço atual/original de cada vínculo do anúncio, leitura final dos
    vínculos que não têm preço para conferir). `folga`: a pausa entre os
    get_model_list da loja."""
    status = str(item.get("item_status") or "").strip().upper()
    ativo = (status == _STATUS_ATIVO) if status else None
    if status in _STATUS_APAGADO:
        apagado = LeituraAnuncio(preco=None, ativo=False, motivo="anúncio excluído na Shopee")
        return {}, {v.id: apagado for v in vinculos}

    modelos: dict[int, dict] = {}
    falha_modelos: str | None = None
    if item.get("has_model") and any(_modelo(v) for v in vinculos):
        await folga.esperar_a_vez()
        try:
            modelos = _modelos_por_id(await _com_novas_tentativas(cliente.get_model_list, item_id))
        except Exception as exc:  # noqa: BLE001 — erro pontual vira motivo
            if _erro_da_conta(exc):
                raise
            falha_modelos = _motivo_falha("falha ao ler as variações", exc)

    vivos: dict[UUID, _PrecoVivo] = {}
    sem_preco: dict[UUID, LeituraAnuncio] = {}
    for v in vinculos:
        modelo = _modelo(v)
        info, motivo = _price_info_do_vinculo(item, modelo, modelos, falha_modelos)
        atual, original = _precos(info)
        if motivo is None and atual is None:
            motivo = "a Shopee não mandou o preço"
        if motivo is not None:
            sem_preco[v.id] = LeituraAnuncio(preco=None, ativo=ativo, motivo=motivo)
        else:
            vivos[v.id] = _PrecoVivo(item_id, modelo, atual, original, ativo)
    return vivos, sem_preco


def _price_info_do_vinculo(
    item: dict, modelo: int, modelos: dict[int, dict], falha_modelos: str | None
) -> tuple[Any, str | None]:
    """O `price_info` que vale para o vínculo — o do anúncio sem variação ou o
    da variação dele —, ou o motivo de não haver."""
    tem_variacao = bool(item.get("has_model"))
    if not modelo:
        if tem_variacao:
            return None, "anúncio com variações e vínculo sem a variação"
        return item.get("price_info"), None
    if not tem_variacao:
        return None, "variação não encontrada (anúncio sem variações)"
    if falha_modelos:
        return None, falha_modelos
    if modelo not in modelos:
        return None, "variação não encontrada no anúncio"
    return modelos[modelo].get("price_info"), None


async def _ler_promocoes(
    cliente: Any, item_ids: list[int]
) -> tuple[dict[int, list[dict]], dict[int, str]]:
    """Promoções dos anúncios em lotes de 50 → (promoções por item, motivo por
    item sem leitura). Erro da conta levanta."""
    promocoes: dict[int, list[dict]] = {}
    falhas: dict[int, str] = {}
    for lote in _lotes(item_ids):
        try:
            lidas, recusas = await _com_novas_tentativas(cliente.promocoes_dos_anuncios, lote)
        except Exception as exc:  # noqa: BLE001 — erro pontual vira motivo
            if _erro_da_conta(exc):
                raise
            falhas.update(dict.fromkeys(lote, _motivo_falha("promoções não lidas", exc)))
            continue
        promocoes.update(lidas)
        for item_id in lote:
            if item_id not in lidas:
                recusa = (recusas.get(item_id) or "")[:_TAMANHO_DO_ERRO]
                falhas[item_id] = "promoções não lidas" + (f" ({recusa})" if recusa else "")
    return promocoes, falhas


def _conferir(
    vivo: _PrecoVivo,
    promocoes: dict[int, list[dict]],
    sem_promocoes: dict[int, str],
    agora: int,
) -> LeituraAnuncio:
    """A leitura final do vínculo: o `current_price` só confere quando é o do
    Desconto de Loja, ou quando não há promoção nenhuma."""
    if not _abaixo_do_original(vivo):
        return LeituraAnuncio(preco=vivo.atual, ativo=vivo.ativo, motivo="sem desconto ativo")
    if vivo.item_id in sem_promocoes:
        return _nao_confere(vivo, sem_promocoes[vivo.item_id])
    tipos = [
        str(p.get("promotion_type") or "").strip().lower()
        for p in promocoes.get(vivo.item_id, [])
        if _vale_para(p, vivo.modelo) and _em_andamento(p, agora)
    ]
    if not tipos and vivo.original is None:
        return LeituraAnuncio(preco=vivo.atual, ativo=vivo.ativo, motivo="sem desconto ativo")
    motivo = _quem_manda_no_preco(tipos)
    if motivo is not None:
        return _nao_confere(vivo, motivo)
    return LeituraAnuncio(preco=vivo.atual, ativo=vivo.ativo)


def _quem_manda_no_preco(tipos: list[str]) -> str | None:
    """None quando o preço da vitrine é o do Desconto de Loja; senão, o motivo
    de não conferir."""
    for trecho, motivo in _POR_CIMA_DO_DESCONTO:
        if any(trecho in t for t in tipos):
            return motivo
    estranhas = [
        t
        for t in tipos
        if t not in _TIPOS_DESCONTO_LOJA and not any(x in t for x in _NAO_MEXEM_NO_PRECO)
    ]
    if estranhas:
        return f"em promoção não reconhecida ({estranhas[0] or 'sem tipo'}) — não conferido"
    if not any(t in _TIPOS_DESCONTO_LOJA for t in tipos):
        return "preço abaixo do original sem Desconto de Loja — não conferido"
    return None


def _nao_confere(vivo: _PrecoVivo, motivo: str) -> LeituraAnuncio:
    # O preço da vitrine fica como informação: não é o que o envio grava.
    return LeituraAnuncio(preco=None, preco_promocional=vivo.atual, ativo=vivo.ativo, motivo=motivo)


def _abaixo_do_original(vivo: _PrecoVivo) -> bool:
    return vivo.original is None or vivo.atual < vivo.original


def _vale_para(promocao: dict, modelo: int) -> bool:
    """A promoção é do anúncio todo (sem `model_id`) ou desta variação."""
    da_promocao = _inteiro(promocao.get("model_id"))
    return not da_promocao or not modelo or da_promocao == modelo


def _em_andamento(promocao: dict, agora: int) -> bool:
    """A promoção vale AGORA. Só fica de fora a que se sabe agendada ou
    encerrada; na dúvida, conta como valendo (e o anúncio não confere)."""
    if str(promocao.get("promotion_staging") or "").strip().lower() == _ETAPA_AGENDADA:
        return False
    inicio = _inteiro(promocao.get("start_time"))
    fim = _inteiro(promocao.get("end_time"))
    return not (inicio and inicio > agora) and not (fim and fim <= agora)


def _precos(info: Any) -> tuple[Decimal | None, Decimal | None]:
    """(current_price, original_price) do primeiro `price_info` (lista ou dict)."""
    if isinstance(info, list):
        info = next((i for i in info if isinstance(i, dict)), None)
    if not isinstance(info, dict):
        return None, None
    return _positivo(info.get("current_price")), _positivo(info.get("original_price"))


def _modelos_por_id(resposta: Any) -> dict[int, dict]:
    modelos = resposta.get("model") if isinstance(resposta, dict) else None
    saida: dict[int, dict] = {}
    for m in modelos or []:
        model_id = _inteiro(m.get("model_id")) if isinstance(m, dict) else None
        if model_id is not None:
            saida[model_id] = m
    return saida


def _erro_da_conta(exc: BaseException) -> bool:
    # Nesse caminho só o refresh do token faz `raise_for_status`: o
    # HTTPStatusError é a renovação recusada (token morto, chave do app).
    if isinstance(exc, httpx.HTTPStatusError):
        return True
    texto = str(exc).lower()
    return any(m in texto for m in _MARCAS_ERRO_CONTA)


def _motivo_falha(o_que: str, exc: BaseException) -> str:
    return f"{o_que}: {str(exc)[:_TAMANHO_DO_ERRO] or type(exc).__name__}"


def _lotes(ids: list[int]) -> Iterator[list[int]]:
    for inicio in range(0, len(ids), LOTE_SHOPEE):
        yield ids[inicio : inicio + LOTE_SHOPEE]


def _modelo(vinculo: ProductLink) -> int:
    # Como o envio: variação vazia, "0" ou estranha = o anúncio sem variação.
    return _inteiro(vinculo.variation_id) or 0


def _inteiro(valor: Any) -> int | None:
    """Inteiro positivo, ou None."""
    try:
        numero = int(str(valor).strip())
    except (TypeError, ValueError):
        return None
    return numero if numero > 0 else None


def _positivo(valor: Any) -> Decimal | None:
    reais = dinheiro(valor)
    return reais if reais is not None and reais > 0 else None
