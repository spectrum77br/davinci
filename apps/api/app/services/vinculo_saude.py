"""Saúde do vínculo anúncio → produto: morto e SKU trocado.

Eduardo, 28/09/2026: "estamos deixando links mortos" e "quando trocamos o sku
dentro do anúncio (dg053.ci → dg053.sp) nosso sistema fica travado no link
velho e não muda, fazendo assim ficar sem estoque".

Morto = o marketplace disse que o anúncio (ou a variação) não existe mais ou
foi encerrado/bloqueado de vez. Vínculo morto:
- não recebe mais envio de estoque (eram ~35 mil tentativas inúteis por dia,
  que ainda faziam o produto inteiro repetir a rodada e estourar o Bling);
- aparece como "morto" na tela e sai sozinho depois de DIAS_ATE_REMOVER;
- volta a viver se o anúncio reaparecer ativo (varredura diária) ou se um
  envio forçado der certo.

Parado NÃO é morto: em revisão, inativo por moderação e pausado seguem como
estão — o anúncio pode voltar.
"""

from __future__ import annotations

import re

DIAS_ATE_REMOVER = 30

# (código ou pedaço do erro, motivo que a tela mostra)
_ML = {
    "ml_item_not_found": "anúncio excluído no Mercado Livre",
    "ml_listing_closed": "anúncio encerrado no Mercado Livre",
}
_AMAZON = {"amazon_sku_not_found": "oferta não existe mais na Amazon"}


def motivo_morto(platform: str, error_code: str | None, error_detail: str | None) -> str | None:
    """Motivo legível se este resultado de envio diz que o anúncio morreu."""
    code = (error_code or "").lower()
    detalhe = (error_detail or "").lower()
    if platform == "ml":
        return _ML.get(code)
    if platform == "shopee":
        if "model id not exist" in detalhe or "model_id not exist" in detalhe:
            return "variação excluída na Shopee"
        # "delist"/"off-shelf" (anúncio só tirado do ar) NÃO entra: o vendedor
        # pode relistar.
        if any(k in detalhe for k in ("abnormal", "banned", "deleted", "removed", "not exist")):
            return "anúncio bloqueado ou excluído na Shopee"
        return None
    if platform == "tiktok":
        if code == "tiktok_status_bloqueado" or (
            "must be in one of these statuses" in detalhe or "change the product to one of these" in detalhe
        ):
            return "produto congelado ou excluído no TikTok"
        return None
    if platform == "amazon":
        return _AMAZON.get(code)
    if platform == "magalu":
        return "anúncio não existe mais na Magalu" if code == "magalu_sku_not_found" else None
    return None


# Bloqueio da Shopee ("status is abnormal") e produto congelado no TikTok às
# vezes voltam: medido em 28/09, 30 de 4.040 anúncios Shopee voltaram a aceitar
# estoque no mês (TikTok: nenhum). Para um erro isolado não derrubar anúncio
# que estava vendendo, esses só viram morto se não houve envio certo nas
# últimas CARENCIA_HORAS. Excluído/encerrado (ML, Amazon, Magalu, variação da
# Shopee) é definitivo e marca na hora.
CARENCIA_HORAS = 24
_COM_CARENCIA = {
    "anúncio bloqueado ou excluído na Shopee",
    "produto congelado ou excluído no TikTok",
}


def precisa_carencia(motivo: str | None) -> bool:
    return motivo in _COM_CARENCIA


def eh_duplicado(motivo: str | None) -> bool:
    """Duplicado marcado pela migration 0333: fica morto até ser apagado —
    nem envio certo nem a varredura o fazem voltar, e o motivo não é trocado."""
    return (motivo or "").startswith(PREFIXO_DUPLICADO)


def norm_sku(sku: str | None) -> str:
    """Mesma normalização do Vincular Automático (auto_link._norm_sku)."""
    return re.sub(r"\s+", "", (sku or "").strip()).lower()


# Mesmo critério em SQL, para a migration marcar o que JÁ está morto hoje
# (lido do último erro gravado no vínculo).
SQL_MOTIVO_MORTO = r"""
CASE
  WHEN platform = 'ml' AND last_error LIKE 'ml_item_not_found%' THEN 'anúncio excluído no Mercado Livre'
  WHEN platform = 'ml' AND last_error LIKE 'ml_listing_closed%' THEN 'anúncio encerrado no Mercado Livre'
  WHEN platform = 'shopee' AND lower(last_error) LIKE '%model id not exist%' THEN 'variação excluída na Shopee'
  WHEN platform = 'shopee' AND (lower(last_error) LIKE '%abnormal%' OR lower(last_error) LIKE '%banned%'
                                OR lower(last_error) LIKE '%deleted%' OR lower(last_error) LIKE '%removed%'
                                OR lower(last_error) LIKE '%not exist%')
       THEN 'anúncio bloqueado ou excluído na Shopee'
  WHEN platform = 'tiktok' AND (lower(last_error) LIKE '%must be in one of these statuses%'
                                OR lower(last_error) LIKE '%change the product to one of these%')
       THEN 'produto congelado ou excluído no TikTok'
  WHEN platform = 'amazon' AND last_error LIKE 'amazon_sku_not_found%' THEN 'oferta não existe mais na Amazon'
  WHEN platform = 'magalu' AND last_error LIKE 'magalu_sku_not_found%' THEN 'anúncio não existe mais na Magalu'
END
"""


# ── Apagar vínculo morto, em lotes ────────────────────────────────────────────
# Desde a 0333 sync_logs não tem mais FK para product_links (o log fica com o
# id), então apagar não mexe no histórico. Lotes curtos mesmo assim, e SKIP
# LOCKED para dois pedidos ao mesmo tempo (botão + varredura) não brigarem
# pelos mesmos vínculos nem contarem em dobro.
LOTE_APAGAR = 500

# Duplicado que a migration 0333 marcou: nunca "revive" com envio que dá certo.
PREFIXO_DUPLICADO = "duplicado:"


async def apagar_mortos_em_lotes(
    *,
    antes_de=None,
    integration_ids: list | None = None,
    somente_visiveis: bool = False,
    pausa: float = 0.2,
) -> int:
    """Apaga vínculos mortos (opcionalmente só os mortos antes de `antes_de`,
    só destas contas e/ou só os que a tela mostra). Devolve quantos apagou."""
    import asyncio

    from sqlalchemy import delete, select

    from app.db import session_scope
    from app.models import ProductLink

    total = 0
    while True:
        async with session_scope() as s:
            q = select(ProductLink.id).where(ProductLink.morto_desde.is_not(None))
            if antes_de is not None:
                q = q.where(ProductLink.morto_desde < antes_de)
            if integration_ids is not None:
                q = q.where(ProductLink.integration_id.in_(integration_ids))
            if somente_visiveis:
                q = q.where(condicao_visivel())
            ids = (
                await s.execute(q.limit(LOTE_APAGAR).with_for_update(skip_locked=True))
            ).scalars().all()
            if not ids:
                break
            res = await s.execute(delete(ProductLink).where(ProductLink.id.in_(ids)))
            total += res.rowcount or 0
        await asyncio.sleep(pausa)
    return total


def condicao_visivel():
    """Vínculo de marketplace de conta não arquivada — o que a tela Produtos
    mostra e conta (e, portanto, o que o botão "Remover mortos" apaga)."""
    from sqlalchemy import and_, or_, select

    from app.models import Integration, IntegrationPlatform, ProductLink

    arquivadas = select(Integration.id).where(Integration.archived_at.is_not(None))
    return and_(
        ProductLink.platform != IntegrationPlatform.BLING,
        or_(ProductLink.integration_id.is_(None), ProductLink.integration_id.not_in(arquivadas)),
    )
