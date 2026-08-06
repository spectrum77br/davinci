// Grupos de navegação unificados (pedido do Eduardo, 08/2026): páginas que
// antes eram itens separados no menu lateral agora aparecem como UMA entrada
// no menu + uma barra de abas no topo de cada página do grupo (RouteTabs).
// As ROTAS continuam as mesmas — links salvos, permissões por página e
// middleware não mudam; só a navegação visível fica mais compacta.
import type { Resource } from '~/composables/useCan'

export type RouteTab = {
  to: string
  label: string
  /** Permissão de view exigida pra VER a aba (admin sempre vê). */
  resource?: Resource
  /** Só admins veem a aba. */
  adminOnly?: boolean
}

type UserLike = {
  role?: string | null
  permissions?: Record<string, Record<string, boolean> | undefined> | null
} | null | undefined

/** Abas que ESTE usuário pode ver (mesma regra do useCan: admin vê tudo). */
export function allowedTabs(tabs: RouteTab[], user: UserLike): RouteTab[] {
  if (!user) return []
  const isAdmin = user.role === 'admin'
  return tabs.filter((t) => {
    if (t.adminOnly) return isAdmin
    if (t.resource) return isAdmin || user.permissions?.[t.resource]?.view === true
    return true
  })
}

// ── Admin: Usuários é a entrada; Permissões e Tarefas viram abas dentro.
// Tarefas NÃO é adminOnly: operador continua com o item "Tarefas" próprio
// no menu (pra ele a barra some — só 1 aba visível).
export const TABS_USUARIOS: RouteTab[] = [
  { to: '/users', label: 'Usuários', adminOnly: true },
  { to: '/permissoes', label: 'Permissões', adminOnly: true },
  { to: '/tarefas', label: 'Tarefas' },
]

export const TABS_CADASTROS: RouteTab[] = [
  { to: '/companies', label: 'Empresas', resource: 'empresa' },
  { to: '/cadastros', label: 'Cadastros', resource: 'cadastro' },
  { to: '/store-info', label: 'Lojas', resource: 'lojas_info' },
  { to: '/admin/segments', label: 'Segmentos', resource: 'segmentos' },
]

export const TABS_NF: RouteTab[] = [
  { to: '/nf-cadastros', label: 'NF (Faturador)', resource: 'nf_faturador' },
  { to: '/nf-faturamento', label: 'Faturamento NF', resource: 'nf_faturamento' },
]

export const TABS_SISTEMA: RouteTab[] = [
  { to: '/sincronizacoes', label: 'Sincronizações', resource: 'sincronizacoes' },
  { to: '/integrations', label: 'Integrações', resource: 'integracoes' },
  { to: '/alertas', label: 'Alertas', resource: 'alertas' },
]
