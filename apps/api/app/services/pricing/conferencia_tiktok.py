"""Leitor do preço VIVO do TikTok Shop para a Conferência de preços da Tabela
(02/11/2026). Interface e regra de ouro em `conferencia_leitores`.

O que o envio grava (`TikTokClient.update_discount_price`, chamado pelo push):
NUNCA o preço base — o `activity_price_amount` da Promoção de Preço Fixo
(FIXED_PRICE) em andamento que tem o produto/SKU (no SKU ou no produto,
conforme o `product_level` da promoção), em reais com 2 casas; produto em
Oferta Relâmpago (FLASHSALE) é pulado. A leitura segue o mesmo caminho: a
busca + o detalhe das promoções que o envio usa (`promocoes_em_andamento`) e
o mesmo casamento produto/SKU (`_match_product_entry`).
  - SKU numa Preço Fixo → preco = o preço dela (a primeira na ordem da busca,
    como no envio); Oferta Relâmpago por cima vai em `preco_promocional`;
  - só em Oferta Relâmpago → não confere (o envio pula);
  - em outro tipo de promoção (desconto percentual) → não confere: o preço
    final não vem pronto;
  - fora de promoção → o preço base do SKU (GET do produto, o SKU DO VÍNCULO,
    nunca o primeiro da lista), com o motivo "sem promoção ativa".
Valores em reais: a API 202309 manda texto com casas ("1226.7", "299.00").
Inteiro grande demais é ambíguo (centavos?) e não confere — a heurística do
`get_listing_price` dividiria por 100 e erraria se fossem reais.

SÓ LEITURA. A busca das promoções que falha levanta: sem ela, nenhum SKU da
loja tem como ser conferido. O detalhe de UMA promoção que não veio (o
cliente tenta de novo a falha passageira) não derruba a loja: o SKU achado
nas outras confere, e o que está fora delas fica "promoção da loja não lida"
(ele pode estar justo na que faltou).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

import structlog

from app.models import ProductLink
from app.services.marketplaces.tiktok import TikTokClient
from app.services.pricing.conferencia_leitores import LeituraAnuncio, dinheiro, registrar

logger = structlog.get_logger()

# `activity_type` das promoções que o envio conhece.
PRECO_FIXO = "FIXED_PRICE"
RELAMPAGO = "FLASHSALE"
# `product_level` da promoção com preço por SKU (no outro, o preço é do produto).
_NIVEL_SKU = "VARIATION"
_MOEDA = "BRL"
# Inteiro deste tamanho é mais provável centavos que reais (a heurística do
# `get_listing_price`): em vez de adivinhar, não confere.
_TETO_INTEIRO_REAIS = Decimal("100000")
# `status` do produto (API 202309; LIVE/ACTIVE das versões antigas).
_STATUS_ATIVO = frozenset({"ACTIVATE", "LIVE", "ACTIVE"})
_STATUS_INATIVO = frozenset({"SELLER_DEACTIVATED", "PLATFORM_DEACTIVATED", "FREEZE", "DELETED"})
_STATUS_APAGADO = "DELETED"
# Recusa da CONTA na leitura do produto (HTTP 401/403, token vencido mesmo
# depois da renovação — `_EXPIRED_CREDS_CODES` do cliente —, falta de escopo
# 105005): levanta, e a conferência marca a conta inteira.
_CODIGOS_CONTA = frozenset({401, 403, 105002, 36009005, 105005})
_TAMANHO_DO_ERRO = 120


@registrar("tiktok")
async def ler_precos_tiktok(
    cliente: Any, vinculos: list[ProductLink]
) -> dict[UUID, LeituraAnuncio]:
    """Uma `LeituraAnuncio` por vínculo da loja (chave = `ProductLink.id`).
    `external_id` = product_id; `variation_id` = sku_id."""
    leituras: dict[UUID, LeituraAnuncio] = {}
    a_ler: list[tuple[ProductLink, str, str]] = []
    for vinculo in vinculos:
        produto_id = (vinculo.external_id or "").strip()
        if produto_id:
            a_ler.append((vinculo, produto_id, (vinculo.variation_id or "").strip()))
        else:
            leituras[vinculo.id] = LeituraAnuncio(preco=None, motivo="vínculo sem o ID do produto")
    if not a_ler:
        return leituras

    atividades = await cliente.promocoes_em_andamento()
    # Promoção sem detalhe: o SKU que não aparece nas outras pode estar nela.
    faltou_detalhe = any(detalhe is None for _, detalhe in atividades)
    fora_de_promocao: dict[str, list[tuple[ProductLink, str]]] = {}
    for vinculo, produto_id, sku_id in a_ler:
        leitura = _pela_promocao(atividades, produto_id, sku_id)
        if leitura is not None:
            leituras[vinculo.id] = leitura
        elif faltou_detalhe:
            leituras[vinculo.id] = LeituraAnuncio(
                preco=None, motivo="promoção da loja não lida — não conferido"
            )
        else:
            fora_de_promocao.setdefault(produto_id, []).append((vinculo, sku_id))

    # Um GET por produto, para todos os SKUs dele que estão fora de promoção.
    for produto_id, pares in fora_de_promocao.items():
        produto, motivo = await _ler_produto(cliente, produto_id)
        for vinculo, sku_id in pares:
            leituras[vinculo.id] = (
                _pelo_preco_base(produto, sku_id)
                if produto is not None
                else LeituraAnuncio(preco=None, motivo=motivo)
            )
    return leituras


def _pela_promocao(
    atividades: list[tuple[dict, dict | None]], produto_id: str, sku_id: str
) -> LeituraAnuncio | None:
    """A leitura pelo que as promoções em andamento dizem do SKU — ou None
    quando ele não está em nenhuma."""
    fixo: dict | None = None
    relampago: dict | None = None
    outro_tipo: str | None = None
    for resumo, detalhe in atividades:
        if not detalhe:
            continue
        entrada = _entrada_do_sku(detalhe, produto_id, sku_id)
        if entrada is None:
            continue
        tipo = str(resumo.get("activity_type") or detalhe.get("activity_type") or "").upper()
        if tipo == PRECO_FIXO:
            fixo = fixo if fixo is not None else entrada
        elif tipo == RELAMPAGO:
            relampago = relampago if relampago is not None else entrada
        elif outro_tipo is None:
            outro_tipo = tipo or "sem tipo"

    # O preço da Oferta Relâmpago é o da vitrine enquanto ela dura (informativo).
    vitrine = _preco_da_promocao(relampago)[0] if relampago is not None else None
    if fixo is not None:
        preco, motivo = _preco_da_promocao(fixo)
        if motivo is None and relampago is not None:
            motivo = "em Oferta Relâmpago (vale o Preço Fixo)"
        return LeituraAnuncio(preco=preco, preco_promocional=vitrine, motivo=motivo)
    if relampago is not None:
        return LeituraAnuncio(
            preco=None,
            preco_promocional=vitrine,
            motivo="em Oferta Relâmpago — o envio pula, não conferido",
        )
    if outro_tipo is not None:
        return LeituraAnuncio(
            preco=None, motivo=f"em promoção {outro_tipo} — leitura não suportada"
        )
    return None


def _entrada_do_sku(detalhe: dict, produto_id: str, sku_id: str) -> dict | None:
    """A entrada da promoção que tem o preço do SKU, casada como no envio
    (`TikTokClient._match_product_entry`): no nível VARIATION, a do SKU; no
    nível do produto, a do produto (o preço vale para todos os SKUs dele)."""
    produto = TikTokClient._match_product_entry(detalhe, produto_id, sku_id)
    if produto is None:
        return None
    if str(detalhe.get("product_level") or "").upper() != _NIVEL_SKU:
        return produto
    skus = [s for s in produto.get("skus") or [] if isinstance(s, dict)]
    return next((s for s in skus if str(s.get("id") or "") == sku_id), None)


def _preco_da_promocao(entrada: dict) -> tuple[Decimal | None, str | None]:
    """(preço, motivo de não ter) da entrada da promoção: `activity_price`
    {amount, currency} na leitura; o mesmo nome da escrita
    (`activity_price_amount`) se vier achatado."""
    bruto = entrada.get("activity_price")
    if isinstance(bruto, dict):
        return _reais(bruto.get("amount"), bruto.get("currency"))
    return _reais(entrada.get("activity_price_amount"), None)


async def _ler_produto(cliente: Any, produto_id: str) -> tuple[dict | None, str | None]:
    """(dados do produto, motivo de não ter). Recusa da conta levanta."""
    try:
        resposta = await cliente.produto_bruto(produto_id)
    except Exception as exc:  # noqa: BLE001 — rede: pontual, vira motivo
        logger.info("conferencia_tiktok_produto_falhou", produto_id=produto_id, erro=str(exc)[:200])
        return None, f"falha ao ler o produto: {str(exc)[:_TAMANHO_DO_ERRO] or type(exc).__name__}"
    if not isinstance(resposta, dict):
        return None, "resposta estranha do TikTok"
    codigo = resposta.get("code")
    if codigo == 0:
        dados = resposta.get("data")
        return (dados if isinstance(dados, dict) else {}), None
    mensagem = str(resposta.get("message") or "")[:_TAMANHO_DO_ERRO]
    if codigo in _CODIGOS_CONTA:
        raise RuntimeError(f"TikTok recusou a leitura do produto {produto_id}: {codigo} {mensagem}")
    return None, f"produto não lido no TikTok ({codigo}: {mensagem})"


def _pelo_preco_base(produto: dict, sku_id: str) -> LeituraAnuncio:
    """O preço base do SKU do vínculo (fora de promoção)."""
    status = str(produto.get("status") or "").strip().upper()
    ativo = _ativo(status)
    if status == _STATUS_APAGADO:
        return LeituraAnuncio(preco=None, ativo=False, motivo="produto excluído no TikTok")
    skus = [s for s in produto.get("skus") or [] if isinstance(s, dict)]
    if sku_id:
        sku = next((s for s in skus if str(s.get("id") or "") == sku_id), None)
    else:
        # Vínculo sem SKU só serve no produto de SKU único (como o
        # `get_listing_snapshot`): nunca o primeiro de vários.
        sku = skus[0] if len(skus) == 1 else None
    if sku is None:
        motivo = (
            "SKU não encontrado no anúncio" if sku_id else "vínculo sem SKU num produto com vários"
        )
        return LeituraAnuncio(preco=None, ativo=ativo, motivo=motivo)
    preco_sku = sku.get("price") if isinstance(sku.get("price"), dict) else {}
    # `sale_price` é o que a leitura traz (SKU real do piloto de 30/09:
    # {"currency": "BRL", "sale_price": "1226.7"}) e o que o `update_price`
    # grava junto do `amount`.
    valor = preco_sku.get("sale_price")
    if valor is None or valor == "":
        valor = preco_sku.get("amount")
    preco, motivo = _reais(valor, preco_sku.get("currency"))
    return LeituraAnuncio(preco=preco, ativo=ativo, motivo=motivo or "sem promoção ativa")


def _reais(valor: Any, moeda: Any) -> tuple[Decimal | None, str | None]:
    """(reais com 2 casas, motivo de não ter). Texto com casas é reais sem
    dúvida; inteiro grande demais pode ser centavos — não confere."""
    if moeda and str(moeda).strip().upper() != _MOEDA:
        return None, f"moeda {moeda} — leitura não suportada"
    if valor is None or valor == "":
        return None, "o TikTok não mandou o preço — leitura não suportada"
    texto = str(valor).strip()
    reais = dinheiro(texto)
    if reais is None or reais <= 0:
        return None, f"preço inválido do TikTok ({texto[:20]})"
    if "." not in texto and reais >= _TETO_INTEIRO_REAIS:
        return None, f"valor ambíguo ({texto[:20]}, centavos?) — leitura não suportada"
    return reais, None


def _ativo(status: str) -> bool | None:
    if status in _STATUS_ATIVO:
        return True
    if status in _STATUS_INATIVO:
        return False
    return None
