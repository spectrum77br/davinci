import { computed } from 'vue'

export type Resource =
  | 'produtos'
  | 'anuncios'
  | 'tabela_precos'
  | 'tabela_precos_contas'
  | 'tabela_precos_produtos'
  | 'tabela_precos_concorrencia'
  | 'margem'
  | 'faturamento'
  | 'controle_estoque'
  | 'financeiro_consorcio'
  | 'financeiro_suprimentos'
  | 'financeiro_simulacao'
  | 'financeiro_dnp'
  | 'importacao'
  | 'devolucoes'
  | 'reembolso'
  | 'logistica'
  | 'notas_fiscais'
  | 'sincronizacoes'
  | 'sync_logs'
  | 'integracoes'
  | 'alertas'
  | 'empresa'
  | 'cadastro'
  | 'lojas_info'
  | 'nf_faturador'
  | 'nf_faturamento'
  | 'segmentos'
  | 'usuarios'
  | 'permissoes'
  | 'configuracoes'

export type Action = 'view' | 'edit' | 'delete'

export type ResourceGroup = {
  label: string
  resources: Resource[]
}

// Ordem e agrupamento ESPELHAM a barra lateral (components/AppSidebar.vue):
// Operação → Pós-venda → Financeiro → Suprimentos → Sistema → Cadastros →
// Admin. Manter os dois em sincronia pra a tela de Permissões refletir o menu.
export const RESOURCE_GROUPS: ResourceGroup[] = [
  {
    label: 'Operação',
    resources: [
      'produtos',
      'anuncios',
      'tabela_precos',
      'tabela_precos_contas',
      'tabela_precos_produtos',
      'tabela_precos_concorrencia',
      'margem',
      'faturamento',
      'controle_estoque',
    ],
  },
  {
    label: 'Pós-venda',
    resources: ['devolucoes', 'reembolso', 'logistica', 'notas_fiscais'],
  },
  {
    // Financeiro = só Consórcio (na tela de Permissões). Valuation é
    // admin-only — não aparece aqui. Certificações, Importação, Simulação
    // e DNP são do ciclo de compra/importação e foram pra Suprimentos (menu).
    label: 'Financeiro',
    resources: ['financeiro_consorcio'],
  },
  {
    // Suprimentos = ciclo de compra/importação. `financeiro_suprimentos` é a
    // página "Certificações" (rota /financeiro/suprimentos no menu).
    label: 'Suprimentos',
    resources: [
      'financeiro_suprimentos',
      'importacao',
      'financeiro_simulacao',
      'financeiro_dnp',
    ],
  },
  {
    label: 'Sistema',
    resources: ['sincronizacoes', 'sync_logs', 'integracoes', 'alertas'],
  },
  {
    label: 'Cadastros',
    resources: ['empresa', 'cadastro', 'lojas_info', 'nf_faturador', 'nf_faturamento', 'segmentos'],
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
  faturamento: 'Faturamento',
  controle_estoque: 'Controle de Estoque',
  financeiro_consorcio: 'Consórcio',
  financeiro_suprimentos: 'Certificações',
  financeiro_simulacao: 'Simulação',
  financeiro_dnp: 'DNP',
  importacao: 'Importação',
  devolucoes: 'Devoluções',
  reembolso: 'Reembolso',
  logistica: 'Logística',
  notas_fiscais: 'Notas Fiscais',
  sincronizacoes: 'Sincronizações',
  sync_logs: 'Sync Logs',
  integracoes: 'Integrações',
  alertas: 'Alertas',
  empresa: 'Empresa',
  cadastro: 'Cadastro',
  lojas_info: 'Lojas (info)',
  nf_faturador: 'NF (Faturador)',
  nf_faturamento: 'Faturamento NF',
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
