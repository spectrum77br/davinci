import { computed } from 'vue'

export type Resource =
  | 'produtos'
  | 'anuncios'
  | 'tabela_precos'
  | 'tabela_precos_contas'
  | 'tabela_precos_produtos'
  | 'tabela_precos_concorrencia'
  | 'margem'
  | 'controle_estoque'
  | 'financeiro_consorcio'
  | 'financeiro_suprimentos'
  | 'financeiro_simulacao'
  | 'financeiro_dnp'
  | 'importacao'
  | 'devolucoes'
  | 'reembolso'
  | 'sincronizacoes'
  | 'sync_logs'
  | 'integracoes'
  | 'alertas'
  | 'empresa'
  | 'cadastro'
  | 'lojas_info'
  | 'segmentos'
  | 'usuarios'
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
      'margem',
      'controle_estoque',
      'produtos',
      'anuncios',
      'tabela_precos',
      'tabela_precos_contas',
      'tabela_precos_produtos',
      'tabela_precos_concorrencia',
    ],
  },
  {
    // Financeiro = the four import-business planilhas. Margem and
    // Controle de Estoque belong in Operação because they're day-to-day
    // operator tools, not accounting/procurement planilhas.
    label: 'Financeiro',
    resources: [
      'financeiro_consorcio',
      'financeiro_suprimentos',
      'financeiro_simulacao',
      'financeiro_dnp',
    ],
  },
  {
    // Importação = controle de pedidos de importação (módulo separado
    // de Financeiro porque é o ciclo de compra/lote, não os planilhas
    // financeiras propriamente ditas).
    label: 'Importação',
    resources: ['importacao'],
  },
  {
    label: 'Pós-venda',
    resources: ['devolucoes', 'reembolso'],
  },
  {
    label: 'Sistema',
    resources: ['sincronizacoes', 'sync_logs', 'integracoes', 'alertas'],
  },
  {
    label: 'Cadastros',
    resources: ['empresa', 'cadastro', 'lojas_info', 'segmentos'],
  },
  {
    label: 'Admin',
    resources: ['usuarios', 'permissoes', 'configuracoes'],
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
  tabela_precos_concorrencia: 'Tabela Preços — Concorrência',
  margem: 'Margem',
  controle_estoque: 'Controle de Estoque',
  financeiro_consorcio: 'Consórcio',
  financeiro_suprimentos: 'Suprimentos',
  financeiro_simulacao: 'Simulação',
  financeiro_dnp: 'DNP',
  importacao: 'Importação',
  devolucoes: 'Devoluções',
  reembolso: 'Reembolso',
  sincronizacoes: 'Sincronizações',
  sync_logs: 'Sync Logs',
  integracoes: 'Integrações',
  alertas: 'Alertas',
  empresa: 'Empresa',
  cadastro: 'Cadastro',
  lojas_info: 'Lojas (info)',
  segmentos: 'Segmentos',
  usuarios: 'Usuários',
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
