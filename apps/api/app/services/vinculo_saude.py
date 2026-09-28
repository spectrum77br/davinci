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
