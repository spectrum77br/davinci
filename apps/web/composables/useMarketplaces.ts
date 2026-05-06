export type Marketplace =
  | 'ml' | 'shopee' | 'amazon' | 'aliexpress'
  | 'temu' | 'tiktok' | 'shein' | 'magalu' | 'site'

export const MARKETPLACES: Marketplace[] = [
  'ml', 'shopee', 'amazon', 'aliexpress', 'temu', 'tiktok', 'shein', 'magalu', 'site',
]

export const MARKETPLACE_LABELS: Record<Marketplace, string> = {
  ml: 'Mercado Livre',
  shopee: 'Shopee',
  amazon: 'Amazon',
  aliexpress: 'AliExpress',
  temu: 'Temu',
  tiktok: 'TikTok',
  shein: 'Shein',
  magalu: 'Magalu',
  site: 'Site',
}

export const MARKETPLACE_SHORT: Record<Marketplace, string> = {
  ml: 'ML', shopee: 'Shopee', amazon: 'AMZ', aliexpress: 'Ali',
  temu: 'Temu', tiktok: 'TT', shein: 'Shein', magalu: 'Magalu', site: 'Site',
}

export type StoreStatus =
  | 'active' | 'inactive' | 'closing' | 'banned' | 'pending' | 'under_review'

export const STORE_STATUS_LABELS: Record<StoreStatus, string> = {
  active: 'X',
  inactive: 'inativa',
  closing: 'fechar',
  banned: 'banido',
  pending: '?',
  under_review: '?',
}

export const STORE_STATUS_CLASSES: Record<StoreStatus, string> = {
  active: 'text-green-400 font-semibold',
  inactive: 'text-muted-foreground line-through',
  closing: 'text-amber-400',
  banned: 'text-red-400',
  pending: 'text-muted-foreground',
  under_review: 'text-muted-foreground',
}

export function useMarketplaces() {
  return {
    MARKETPLACES,
    MARKETPLACE_LABELS,
    MARKETPLACE_SHORT,
    STORE_STATUS_LABELS,
    STORE_STATUS_CLASSES,
  }
}
