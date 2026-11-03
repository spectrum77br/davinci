"""Leitor do preço VIVO no Mercado Livre, para a Conferência de preços.

O envio da Tabela (`MercadoLivreClient.update_price`) grava o preço em reais
inteiros no anúncio sem variação, ou o MESMO preço em TODAS as variações do
anúncio que tem variação. Então o preço comparável é o da variação do vínculo
(`link.variation_id`) — ou o do anúncio, quando o vínculo não tem variação ou
a variação não existe mais (o envio grava em todas as que existem).

Promoção: com `original_price` acima de `price`, o ML está mostrando o preço
promocional; o preço que o envio gravou é o original. Aí `preco` é o
`original_price` e `preco_promocional` é o que o comprador paga.

Anúncio em que o envio NÃO grava (pula sem PUT) não confere: o preço dele
fica congelado e o (!) nunca sumiria. São os mesmos casos do `update_price`:
encerrado (`closed`), removido por moderação (`under_review` com `forbidden`
no `sub_status`) e de outro tipo que o da coluna (`_conferir_tipo_ml`: o
anúncio virou premium e o vínculo/a coluna é clássico, ou o contrário).

Leitura em lote, de 20 em 20 anúncios (`GET /items?ids=...`, como o
`MercadoLivreClient.itens`). 401/403 no lote = a conta inteira sem acesso
(levanta); qualquer outra falha do lote vira "erro ao ler" só nos anúncios
daquele lote.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any
from uuid import UUID

import httpx
import structlog

from app.models import ProductLink
from app.services.marketplaces.ml import _conferir_tipo_ml
from app.services.pricing.conferencia_leitores import LeituraAnuncio, dinheiro, registrar

logger = structlog.get_logger()

# O multiget do ML aceita no máximo 20 ids por chamada.
LOTE = 20
# Só o que a conferência usa (o anúncio inteiro é bem maior).
CAMPOS = "id,price,original_price,status,sub_status,listing_type_id,variations"
STATUS_ATIVO = "active"
# Estados em que o envio (`MercadoLivreClient.update_price`) pula sem gravar.
STATUS_ENCERRADO = "closed"
STATUS_EM_REVISAO = "under_review"
SUB_STATUS_MODERADO = "forbidden"
HTTP_OK = 200
# Recusa de acesso da conta inteira (o 401 já passou pela renovação do token).
HTTP_ACESSO_RECUSADO = (401, 403)

MOTIVO_NAO_ENCONTRADO = "anúncio não encontrado"
MOTIVO_ERRO_AO_LER = "erro ao ler"
MOTIVO_SEM_PRECO = "anúncio sem preço"
MOTIVO_PROMOCAO = "em promoção"
MOTIVO_VARIACAO_SUMIU = "variação não encontrada; lido o preço do anúncio"
MOTIVO_ENCERRADO = "anúncio encerrado — o envio pula"
MOTIVO_MODERADO = "removido por moderação — o envio pula"


class AcessoRecusadoError(RuntimeError):
    """O ML recusou o acesso da conta inteira (token/permissão)."""


def _sub_status(item: dict[str, Any]) -> set[str]:
    """O `sub_status` do anúncio (uma lista no ML; texto solto também serve)."""
    bruto = item.get("sub_status") or []
    if isinstance(bruto, str):
        bruto = [bruto]
    if not isinstance(bruto, list):
        return set()
    return {str(s).strip().lower() for s in bruto if s}


def leitura_do_item(
    item: dict[str, Any], variation_id: str | None, *, tipo_do_vinculo: str | None = None
) -> LeituraAnuncio:
    """Preço comparável com a Tabela de UM vínculo, a partir do anúncio lido.

    `tipo_do_vinculo` é o `listing_type` gravado no vínculo: o resolvedor do
    envio só põe na célula o vínculo do tipo da conta, então é o tipo que o
    envio confere no anúncio vivo (`tipo_esperado` do `update_price`)."""
    status = str(item.get("status") or "").strip().lower()
    ativo = (status == STATUS_ATIVO) if status else None
    # O envio pula sem gravar: o preço daqui ficou congelado.
    if status == STATUS_ENCERRADO:
        return LeituraAnuncio(preco=None, ativo=False, motivo=MOTIVO_ENCERRADO)
    if status == STATUS_EM_REVISAO and SUB_STATUS_MODERADO in _sub_status(item):
        return LeituraAnuncio(preco=None, ativo=ativo, motivo=MOTIVO_MODERADO)
    outro_tipo = _conferir_tipo_ml(str(item.get("id") or ""), item, tipo_do_vinculo)
    if outro_tipo is not None:
        return LeituraAnuncio(preco=None, ativo=ativo, motivo=outro_tipo.error_detail)
    motivos: list[str] = []
    preco_vivo = dinheiro(item.get("price"))
    if variation_id:
        variacao = next(
            (
                v
                for v in item.get("variations") or []
                if isinstance(v, dict) and str(v.get("id")) == str(variation_id)
            ),
            None,
        )
        if variacao is None:
            motivos.append(MOTIVO_VARIACAO_SUMIU)
        elif dinheiro(variacao.get("price")) is not None:
            preco_vivo = dinheiro(variacao.get("price"))
    if preco_vivo is None:
        return LeituraAnuncio(preco=None, ativo=ativo, motivo=MOTIVO_SEM_PRECO)
    original = dinheiro(item.get("original_price"))
    if original is not None and original > preco_vivo:
        return LeituraAnuncio(
            preco=original,
            preco_promocional=preco_vivo,
            ativo=ativo,
            motivo="; ".join([MOTIVO_PROMOCAO, *motivos]),
        )
    return LeituraAnuncio(preco=preco_vivo, ativo=ativo, motivo="; ".join(motivos) or None)


async def _ler_lote(cliente: Any, ids: list[str]) -> dict[str, dict] | None:
    """{id: anúncio} dos que o ML devolveu com 200 no lote; None = o lote
    inteiro falhou (rede, 429/5xx depois das tentativas do cliente, corpo
    inválido). Levanta `AcessoRecusadoError` em 401/403."""
    try:
        # O mesmo caminho do `MercadoLivreClient.itens` (renova o token no 401
        # e insiste no 429/5xx).
        r = await cliente._request(
            "GET", "/items", params={"ids": ",".join(ids), "attributes": CAMPOS}
        )
    except httpx.TransportError as e:
        logger.warning("conferencia_ml_lote_falhou", erro=type(e).__name__, ids=len(ids))
        return None
    if r.status_code in HTTP_ACESSO_RECUSADO:
        raise AcessoRecusadoError(f"o Mercado Livre recusou o acesso da conta ({r.status_code})")
    if r.status_code != HTTP_OK:
        logger.warning("conferencia_ml_lote_falhou", status=r.status_code, ids=len(ids))
        return None
    try:
        corpo = r.json()
    except ValueError:
        logger.warning("conferencia_ml_lote_falhou", erro="json", ids=len(ids))
        return None
    achados: dict[str, dict] = {}
    for entrada in corpo if isinstance(corpo, list) else []:
        if not isinstance(entrada, dict) or entrada.get("code") != HTTP_OK:
            continue
        body = entrada.get("body")
        if isinstance(body, dict) and body.get("id"):
            achados[str(body["id"]).strip()] = body
    return achados


@registrar("ml")
async def ler_precos_ml(cliente: Any, vinculos: list[ProductLink]) -> dict[UUID, LeituraAnuncio]:
    """Uma `LeituraAnuncio` por vínculo (chave = `ProductLink.id`)."""
    por_anuncio: dict[str, list[ProductLink]] = defaultdict(list)
    for lk in vinculos:
        por_anuncio[str(lk.external_id or "").strip()].append(lk)
    sem_id = por_anuncio.pop("", [])
    saida: dict[UUID, LeituraAnuncio] = {
        lk.id: LeituraAnuncio(preco=None, motivo=MOTIVO_NAO_ENCONTRADO) for lk in sem_id
    }
    ids = list(por_anuncio)
    for inicio in range(0, len(ids), LOTE):
        lote = ids[inicio : inicio + LOTE]
        achados = await _ler_lote(cliente, lote)
        for item_id in lote:
            for lk in por_anuncio[item_id]:
                if achados is None:
                    saida[lk.id] = LeituraAnuncio(preco=None, motivo=MOTIVO_ERRO_AO_LER)
                elif item_id not in achados:
                    saida[lk.id] = LeituraAnuncio(preco=None, motivo=MOTIVO_NAO_ENCONTRADO)
                else:
                    saida[lk.id] = leitura_do_item(
                        achados[item_id], lk.variation_id, tipo_do_vinculo=lk.listing_type
                    )
    return saida
