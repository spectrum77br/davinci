import { computed } from 'vue'

export type Resource =
  | 'produtos'
  | 'anuncios'
  | 'tabela_precos'
  | 'tabela_precos_contas'
  | 'tabela_precos_produtos'
  | 'conciliacao_frete'
  | 'sincronizacoes'
  | 'devolucoes'
  | 'reembolso'
  | 'tarefas'
  | 'margem'
  | 'empresa'
  | 'cadastro'
  | 'permissoes'

export type Action = 'view' | 'edit' | 'delete'

export const RESOURCES: Resource[] = [
  'produtos',
  'anuncios',
  'tabela_precos',
  'tabela_precos_contas',
  'tabela_precos_produtos',
  'conciliacao_frete',
  'sincronizacoes',
  'devolucoes',
  'reembolso',
  'tarefas',
  'margem',
  'empresa',
  'cadastro',
  'permissoes',
]

export const ACTIONS: Action[] = ['view', 'edit', 'delete']

export const RESOURCE_LABELS: Record<Resource, string> = {
  produtos: 'Produtos',
  anuncios: 'Anúncios',
  tabela_precos: 'Tabela de Preços',
  tabela_precos_contas: 'Tabela Preços — Contas',
  tabela_precos_produtos: 'Tabela Preços — Produtos',
  conciliacao_frete: 'Conciliação Frete',
  sincronizacoes: 'Sincronizações',
  devolucoes: 'Devoluções',
  reembolso: 'Reembolso',
  tarefas: 'Tarefas',
  margem: 'Margem',
  empresa: 'Empresa',
  cadastro: 'Cadastro',
  permissoes: 'Permissões',
}

export function useCan(resource: Resource, action: Action) {
  const auth = useAuthStore()
  return computed(() => {
    const u = auth.user
    if (!u) return false
    if (u.role === 'admin') return true
    return u.permissions?.[resource]?.[action] === true
  })
}
