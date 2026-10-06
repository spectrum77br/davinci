import { computed } from 'vue'

export type Resource =
  | 'produtos'
  | 'anuncios'
  | 'marketing'
  | 'marketing_criativos'
  | 'tabela_precos'
  | 'tabela_precos_contas'
  | 'tabela_precos_produtos'
  | 'tabela_precos_concorrencia'
  | 'tabela_precos_todas_contas'
  | 'margem'
  | 'faturamento'
  | 'controle_estoque'
  | 'financeiro_consorcio'
  | 'financeiro_suprimentos'
  | 'financeiro_simulacao'
  | 'financeiro_dnp'
  | 'imobilizado'
  | 'importacao'
  | 'devolucoes'
  | 'reembolso'
  | 'logistica'
  | 'notas_fiscais'
  | 'chamados'
  | 'atendimento'
  | 'sincronizacoes'
  | 'sync_logs'
  | 'integracoes'
  | 'alertas'
  | 'empresa'
  | 'cadastro'
  | 'lojas_info'
  | 'nf_faturador'
  | 'nf_faturamento'
  | 'emissao_servico'
  | 'segmentos'
  | 'marcas'
  | 'redes_sociais'
  | 'email_padroes'
  | 'ouvidoria'
  | 'denuncia'
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
// Ouvidoria → Admin. Manter os dois em sincronia pra a tela de Permissões
// refletir o menu.
export const RESOURCE_GROUPS: ResourceGroup[] = [
  {
    label: 'Operação',
    resources: [
      'produtos',
      'anuncios',
      'marketing',
      'marketing_criativos',
      'tabela_precos',
      'tabela_precos_contas',
      'tabela_precos_produtos',
      'tabela_precos_concorrencia',
      'tabela_precos_todas_contas',
      'margem',
      'faturamento',
      'controle_estoque',
    ],
  },
  {
    label: 'Pós-venda',
    // Atendimento (25/09/2026): caixa única das conversas das lojas com a
    // sugestão da IA. view = ler a fila; edit = responder, mexer no modo da
    // loja, no manual e nas respostas prontas; delete = apagar regra/resposta.
    // SÓ ADMIN POR ENQUANTO (Eduardo, 30/09/2026): fica FORA desta lista, como
    // o Valuation, para ninguém receber a permissão pela tela de Permissões
    // (nem pelo "marcar a coluna toda"). O tipo e o rótulo continuam, e a API
    // exige admin (SO_ADMIN em apps/api/app/routers/atendimento.py). Para
    // abrir para a equipe, 'atendimento' volta para o fim desta lista.
    resources: ['devolucoes', 'reembolso', 'logistica', 'notas_fiscais', 'chamados'],
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
    resources: [
      'empresa', 'cadastro', 'lojas_info', 'nf_faturador', 'nf_faturamento',
      // Emissão de Serviço (28/09/2026): NFS-e pela NFE.io, aba de Cadastros (ao lado de Empresas).
      'emissao_servico', 'segmentos',
      // Marcas e Redes Sociais (15/09/2026) — abas novas do grupo Cadastros.
      'marcas', 'redes_sociais', 'email_padroes',
      // Imobilizado (06/10/2026): bens da empresa. view = vê todos (Gestor);
      // edit = cadastra/edita/transfere; delete = dá baixa (Administrador de
      // patrimônio). Sem view a pessoa vê só os itens dela.
      'imobilizado',
    ],
  },
  {
    // Ouvidoria (21/09/2026) = os robôs de vigilância (Vigia de importação…)
    // e o que eles encontraram. Vinicius: por último no menu, logo acima de
    // Admin. Um recurso só cobre a página inteira (Robôs + Ocorrências):
    // view = olhar; edit = ligar/desligar robô, rodar agora, tratar/ignorar.
    label: 'Ouvidoria',
    // Denúncia (30/09/2026): Anúncios, Denúncias, Casos (+ Jurídico, que pede
    // também Chamados) — item dentro da Ouvidoria. Só leitura.
    resources: ['ouvidoria', 'denuncia'],
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
  marketing: 'Marketing (Ads)',
  marketing_criativos: 'Marketing — Criativos',
  tabela_precos: 'Tabela de Preços',
  tabela_precos_contas: 'Tabela Preços — Contas',
  tabela_precos_todas_contas: 'Tabela Preços — Ver todas as contas',
  tabela_precos_produtos: 'Tabela Preços — Produtos',
  tabela_precos_concorrencia: 'Tabela Preços — Concorrência',
  margem: 'Margem',
  faturamento: 'Faturamento',
  controle_estoque: 'Controle de Estoque',
  financeiro_consorcio: 'Consórcio',
  financeiro_suprimentos: 'Certificações',
  financeiro_simulacao: 'Simulação',
  financeiro_dnp: 'DNP',
  imobilizado: 'Imobilizado (patrimônio)',
  importacao: 'Importação',
  devolucoes: 'Devoluções',
  reembolso: 'Reembolso',
  logistica: 'Logística',
  notas_fiscais: 'Notas Fiscais',
  chamados: 'Chamados',
  atendimento: 'Atendimento',
  sincronizacoes: 'Sincronizações',
  sync_logs: 'Sync Logs',
  integracoes: 'Integrações',
  alertas: 'Alertas',
  empresa: 'Empresa',
  cadastro: 'Cadastro',
  lojas_info: 'Lojas (info)',
  nf_faturador: 'NF (Faturador)',
  nf_faturamento: 'Faturamento NF',
  emissao_servico: 'Emissão de Serviço (NFS-e)',
  segmentos: 'Segmentos',
  marcas: 'Marcas',
  redes_sociais: 'Redes Sociais',
  email_padroes: 'E-mails (padrões)',
  ouvidoria: 'Robôs (Ouvidoria)',
  denuncia: 'Denúncia (Anúncios, Denúncias, Casos)',
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
