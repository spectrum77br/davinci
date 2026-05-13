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
  | 'margem'
  | 'empresa'
  | 'cadastro'
  | 'lojas_info'
  | 'permissoes'
  | 'configuracoes'

export type Action = 'view' | 'edit' | 'delete'

export type ResourceGroup = {
  label: string
  resources: Resource[]
}

export const RESOURCE_GROUPS: ResourceGroup[] = [
  {
    label: 'Operação',
    resources: [
      'produtos',
      'anuncios',
      'tabela_precos',
      'tabela_precos_contas',
      'tabela_precos_produtos',
      'margem',
    ],
  },
  {
    label: 'Pós-venda',
    resources: ['conciliacao_frete', 'devolucoes', 'reembolso'],
  },
  {
    label: 'Sistema',
    resources: ['sincronizacoes'],
  },
  {
    label: 'Cadastros',
    resources: ['empresa', 'cadastro', 'lojas_info'],
  },
  {
    label: 'Admin',
    resources: ['permissoes', 'configuracoes'],
  },
]

export const RESOURCES: Resource[] = RESOURCE_GROUPS.flatMap((g) => g.resources)

export const ACTIONS: Action[] = ['view', 'edit', 'delete']

export const RESOURCE_LABELS: Record<Resource, string> = {
  produtos: 'Produtos',
  anuncios: 'Anúncios',
  tabela_precos: 'Tabela de Preços',
  tabela_precos_contas: 'Tabela Preços — Contas',
  tabela_precos_produtos: 'Tabela Preços — Produtos',
  margem: 'Margem',
  conciliacao_frete: 'Conciliação Frete',
  devolucoes: 'Devoluções',
  reembolso: 'Reembolso',
  sincronizacoes: 'Sincronizações',
  empresa: 'Empresa',
  cadastro: 'Cadastro',
  lojas_info: 'Lojas (info)',
  permissoes: 'Permissões',
  configuracoes: 'Configurações',
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
