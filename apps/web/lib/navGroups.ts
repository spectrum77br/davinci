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
  /** Só quem vem com esta chave `true` no /me — vale até para admin (o
   *  Histórico só abre para quem o Eduardo liberou). */
  soComChave?: 'historico'
  /** Aba que é um grupo de outras abas (17/11/2026: Integrações dentro de
   *  Cadastros, Usuários dentro de Configurações). Aparece se a pessoa vê
   *  alguma delas, leva à primeira que ela vê e fica marcada em todas. */
  abas?: RouteTab[]
  /** Bolinha vermelha da aba (as pendências que o menu lateral já conta). */
  ponto?: 'tarefas' | 'faturas'
}

type UserLike = {
  role?: string | null
  permissions?: Record<string, Record<string, boolean> | undefined> | null
  historico?: boolean | null
} | null | undefined

/** Abas que ESTE usuário pode ver (mesma regra do useCan: admin vê tudo). */
export function allowedTabs(tabs: RouteTab[], user: UserLike): RouteTab[] {
  if (!user) return []
  const isAdmin = user.role === 'admin'
  const visiveis: RouteTab[] = []
  for (const t of tabs) {
    if (t.soComChave && user[t.soComChave] !== true) continue
    if (t.adminOnly && !isAdmin) continue
    if (t.resource && !isAdmin && user.permissions?.[t.resource]?.view !== true) continue
    if (t.abas) {
      const dentro = allowedTabs(t.abas, user)
      if (!dentro.length) continue
      visiveis.push({ ...t, to: dentro[0].to })
      continue
    }
    visiveis.push(t)
  }
  return visiveis
}

/** Rotas em que a aba fica marcada: a dela ou, num grupo, as de todas as abas de dentro. */
export function tabRoutes(t: RouteTab): string[] {
  return t.abas ? t.abas.flatMap(tabRoutes) : [t.to]
}

/** Pendências (bolinha) da aba e das abas de dentro. */
export function tabPontos(t: RouteTab): NonNullable<RouteTab['ponto']>[] {
  return [...(t.ponto ? [t.ponto] : []), ...(t.abas ?? []).flatMap(tabPontos)]
}

// ── Admin: Usuários é a entrada; Permissões e Tarefas viram abas dentro.
// Tarefas NÃO é adminOnly: operador continua com o item "Tarefas" próprio
// no menu (pra ele a barra some — só 1 aba visível).
export const TABS_USUARIOS: RouteTab[] = [
  { to: '/users', label: 'Usuários', adminOnly: true },
  { to: '/permissoes', label: 'Permissões', adminOnly: true },
  { to: '/tarefas', label: 'Tarefas', ponto: 'tarefas' },
]

// ── Configurações (17/11/2026, Novos procedimentos 10): Usuários e Faturas
// saíram do menu e viraram abas de Configurações. A permissão não muda: as
// duas continuam só para admin (a aba Usuários leva junto Permissões e
// Tarefas); quem tem só `configuracoes` vê a tela de antes, sem a barra.
// Faturas é o painel das faturas a pagar, não o Faturamento das vendas.
export const TABS_CONFIGURACOES: RouteTab[] = [
  { to: '/configuracoes', label: 'Configurações', resource: 'configuracoes' },
  { to: '/users', label: 'Usuários', adminOnly: true, abas: TABS_USUARIOS },
  { to: '/faturas', label: 'Faturas', adminOnly: true, ponto: 'faturas' },
]

// ── Integrações (17/11/2026, Novos procedimentos 9): era o item Integrações
// de Sistema (Sincronizações · Integrações · Alertas) e virou a aba
// Integrações de Cadastros, com o Histórico dentro. Mesmas permissões de
// antes; o Histórico continua só para quem o Eduardo liberou (nem todo admin).
// Precisa vir antes de TABS_CADASTROS, que a usa.
export const TABS_INTEGRACOES: RouteTab[] = [
  { to: '/integrations', label: 'Integrações', resource: 'integracoes' },
  { to: '/sincronizacoes', label: 'Sincronizações', resource: 'sincronizacoes' },
  { to: '/alertas', label: 'Alertas', resource: 'alertas' },
  { to: '/historico', label: 'Histórico', soComChave: 'historico' },
]

export const TABS_CADASTROS: RouteTab[] = [
  { to: '/companies', label: 'Empresas', resource: 'empresa' },
  // Contabilidade (ex-Emissão de Serviço, 23/10/2026) mora ao lado de Empresas — Eduardo,
  // 30/09/2026: "é para ficar lá em cadastros, não em nf faturador". Dentro: Emissão
  // de Serviço (NFS-e pela NFE.io) e Compra.
  { to: '/emissao-servico', label: 'Contabilidade', resource: 'emissao_servico' },
  { to: '/cadastros', label: 'Cadastros', resource: 'cadastro' },
  { to: '/store-info', label: 'Lojas', resource: 'lojas_info' },
  { to: '/admin/segments', label: 'Segmentos', resource: 'segmentos' },
  // Marcas e Redes Sociais (Eduardo, 15/09/2026): cadastro das marcas da
  // operação (INPI, domínio) e das contas por plataforma — que depois recebem
  // o envio automático de vídeos.
  { to: '/marcas', label: 'Marcas', resource: 'marcas' },
  { to: '/redes-sociais', label: 'Redes Sociais', resource: 'redes_sociais' },
  // Padrões de e-mail por marca e canal (logo, assinatura, prévia, teste).
  { to: '/email-padroes', label: 'E-mails', resource: 'email_padroes' },
  // Biblioteca de legendas do robô de postagem (Eduardo, 16/09/2026): cadastro
  // DA MARCA, como os padrões de e-mail — por isso mora aqui e não na aba
  // Criativos. A permissão é a da tela dos criativos (`marketing_criativos`),
  // que é quem manda no robô de postagem.
  { to: '/legendas', label: 'Legendas', resource: 'marketing_criativos' },
  // Imobilizado (06/10/2026): bens da empresa e quem responde por cada um.
  // Sem `resource` de propósito: quem não tem a permissão vê só os itens de
  // que é responsável (a tela e a API filtram). Admin/Gestor veem todos.
  { to: '/imobilizado', label: 'Imobilizado' },
  // Envios Threema (tarefa 16, 19/11/2026): cada aviso do Threema — quem
  // recebe, se está ligado, como foi o último envio. Só admin. Rota fora de
  // /cadastros/ de propósito: a aba Cadastros acende em /cadastros/*.
  { to: '/envios-threema', label: 'Envios Threema', adminOnly: true },
  // Integrações (17/11/2026): Integrações · Sincronizações · Alertas · Histórico.
  { to: '/integrations', label: 'Integrações', abas: TABS_INTEGRACOES },
  // Produtos (M-29, 24/11/2026): a lista oficial de categorias e tags de envio
  // (Tipo | Nome), lida pelos filtros do Produtos 2. Criar/editar/excluir só admin.
  { to: '/cadastro-produtos', label: 'Produtos', resource: 'cadastro_produtos' },
  // Centro de custo (M-76, 25/11/2026): Celular, Eletro, Mala e os novos — a fonte do menu
  // "Centro de custo" das Categorias de venda. Vê quem vê Produtos; mexer, só admin.
  { to: '/cadastro-centro-custo', label: 'Centro de custo', resource: 'cadastro_produtos' },
]

export const TABS_NF: RouteTab[] = [
  { to: '/nf-cadastros', label: 'NF (Faturador)', resource: 'nf_faturador' },
  { to: '/nf-faturamento', label: 'Faturamento NF', resource: 'nf_faturamento' },
]
