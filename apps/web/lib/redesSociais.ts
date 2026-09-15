// Constantes das abas Cadastros › Redes Sociais / Marcas / E-mails.
// Espelham app/models/enums.py (RedeSocialPlataforma, VerificacaoStatus,
// EmailContexto, MarcaInpiStatus). Valor novo: acrescentar lá e aqui.

// As 5 redes da planilha do Eduardo, na ordem dela ("seguir bem a planilha").
export type Plataforma = 'instagram' | 'facebook' | 'twitter' | 'tiktok' | 'youtube'

export const PLATAFORMAS: Plataforma[] = ['instagram', 'facebook', 'twitter', 'tiktok', 'youtube']

export const PLATAFORMA_LABELS: Record<Plataforma, string> = {
  instagram: 'Instagram',
  facebook: 'Facebook',
  twitter: 'X (Twitter)',
  tiktok: 'TikTok',
  youtube: 'YouTube',
}

// Link do perfil a partir do @ — usado como sugestão quando `url` está vazio.
const PERFIL_URL: Record<Plataforma, (conta: string) => string> = {
  instagram: (c) => `https://www.instagram.com/${c}/`,
  facebook: (c) => `https://www.facebook.com/${c}`,
  twitter: (c) => `https://x.com/${c}`,
  tiktok: (c) => `https://www.tiktok.com/@${c}`,
  youtube: (c) => `https://www.youtube.com/@${c}`,
}

export function perfilUrl(plataforma: string, conta: string | null | undefined): string | null {
  // O backend grava `conta` sem "@" (schemas/marcas._handle); aqui pode chegar
  // o que o usuário digitou no modal, então tira o "@" também.
  const c = (conta || '').trim().replace(/^@+/, '')
  if (!c) return null
  const f = PERFIL_URL[plataforma as Plataforma]
  return f ? f(c) : null
}

// Situação no INPI (app/models/enums.py::MarcaInpiStatus) → rótulo + pill.
export type InpiStatus = 'nao_registrado' | 'aguardando' | 'registrado' | 'indeferido' | 'expirado'

export const INPI_STATUS: InpiStatus[] = [
  'nao_registrado', 'aguardando', 'registrado', 'indeferido', 'expirado',
]

export const INPI_STATUS_LABELS: Record<InpiStatus, string> = {
  nao_registrado: 'não registrado',
  aguardando: 'aguardando',
  registrado: 'registrado',
  indeferido: 'indeferido',
  expirado: 'expirado',
}

export const INPI_STATUS_PILL: Record<InpiStatus, string> = {
  nao_registrado: 'pill-muted',
  aguardando: 'pill-warning',
  registrado: 'pill-success',
  indeferido: 'pill-danger',
  expirado: 'pill-danger',
}

// Selo de verificado (Meta Verified) — conta social ou WhatsApp da marca.
export type VerificacaoStatus = 'nao_solicitado' | 'em_andamento' | 'verificado' | 'recusado'

export const VERIFICACAO_STATUS: VerificacaoStatus[] = [
  'nao_solicitado', 'em_andamento', 'verificado', 'recusado',
]

export const VERIFICACAO_LABELS: Record<VerificacaoStatus, string> = {
  nao_solicitado: 'não solicitado',
  em_andamento: 'em andamento',
  verificado: 'verificado',
  recusado: 'recusado',
}

export const VERIFICACAO_PILL: Record<VerificacaoStatus, string> = {
  nao_solicitado: 'pill-muted',
  em_andamento: 'pill-warning',
  verificado: 'pill-success',
  recusado: 'pill-danger',
}

// Fone só dígitos → (11) 98888-7777. Mesma regra do backend (email_marca.formatar_fone).
export function fmtFone(fone: string | null | undefined): string {
  let d = (fone || '').replace(/\D/g, '')
  if (d.startsWith('55') && (d.length === 12 || d.length === 13)) d = d.slice(2)
  if (d.length === 11) return `(${d.slice(0, 2)}) ${d.slice(2, 7)}-${d.slice(7)}`
  if (d.length === 10) return `(${d.slice(0, 2)}) ${d.slice(2, 6)}-${d.slice(6)}`
  return d
}

// Contextos dos padrões de e-mail (app/models/enums.py::EmailContexto):
// SAC + os marketplaces do sistema (mesmos valores de useMarketplaces) + Geral.
export type EmailContexto =
  | 'sac' | 'ml' | 'shopee' | 'amazon' | 'aliexpress' | 'temu' | 'tiktok' | 'shein' | 'magalu'
  | 'site' | 'geral'

export const EMAIL_CONTEXTOS: EmailContexto[] = [
  'sac', 'ml', 'shopee', 'amazon', 'aliexpress', 'temu', 'tiktok', 'shein', 'magalu', 'site', 'geral',
]

export const EMAIL_CONTEXTO_LABELS: Record<EmailContexto, string> = {
  sac: 'SAC',
  ml: 'ML',
  shopee: 'Shopee',
  amazon: 'Amazon',
  aliexpress: 'Aliexpress',
  temu: 'Temu',
  tiktok: 'Tik tok',
  shein: 'Shein',
  magalu: 'Magalu',
  site: 'Site',
  geral: 'Geral',
}

// Placeholders dos templates (app/services/email_marca.py::PLACEHOLDERS).
export const EMAIL_PLACEHOLDERS: { chave: string; descricao: string }[] = [
  { chave: 'marca', descricao: 'nome da marca' },
  { chave: 'empresa', descricao: 'razão social da empresa da assinatura' },
  { chave: 'cnpj', descricao: 'CNPJ formatado' },
  { chave: 'site', descricao: 'site da marca' },
  { chave: 'whatsapp', descricao: 'WhatsApp formatado' },
  { chave: 'whatsapp_link', descricao: 'link wa.me' },
  { chave: 'email_sac', descricao: 'e-mail do SAC' },
  { chave: 'cliente', descricao: 'nome do cliente (na hora do envio)' },
  { chave: 'pedido', descricao: 'número do pedido (na hora do envio)' },
  { chave: 'produto', descricao: 'produto (na hora do envio)' },
  { chave: 'plataforma', descricao: 'marketplace (na hora do envio)' },
]

// Guia "Como verificar" — o DaVinci só REGISTRA o andamento; o pedido é feito
// no app da plataforma pela equipe. Fontes oficiais nos links.
export type GuiaVerificacao = {
  titulo: string
  resumo: string
  requisitos: string[]
  passos: string[]
  links: { label: string; url: string }[]
}

export const VERIFICACAO_GUIA: GuiaVerificacao[] = [
  {
    titulo: 'Instagram e Facebook — Meta Verified para empresas',
    resumo:
      'Assinatura mensal (ou anual) da Meta que dá o selo de verificado à conta profissional do Instagram e/ou à Página '
      + 'do Facebook, com proteção contra perfis falsos e suporte. A assinatura é feita dentro do app (iOS/Android), '
      + 'no celular que tem a conta — o DaVinci só registra o andamento aqui.',
    requisitos: [
      'Conta profissional (empresa) no Instagram / Página no Facebook, com tempo mínimo de existência e atividade (publicações).',
      'Autenticação de dois fatores (2FA) ligada na conta.',
      'Dados da empresa que a Meta valida: nome, endereço, site e telefone — e confirmação do vínculo do solicitante com a empresa (por telefone, e-mail ou domínio).',
      'Cartão para a assinatura (valor por plano, cobrado pela Meta; consulte o preço no app).',
    ],
    passos: [
      'No app do Instagram (ou Facebook), abrir o perfil da marca → Painel profissional → Meta Verified → escolher o plano.',
      'Confirmar os dados da empresa, comprovar o vínculo (telefone/e-mail/domínio) e concluir o pagamento.',
      'A Meta analisa em até 3 dias úteis e avisa por notificação/e-mail — enquanto isso, marque aqui "em andamento" e anote data/protocolo na observação.',
      'Aprovado: marcar "verificado" (o selo vale enquanto a assinatura estiver ativa). Recusado: anotar o motivo em "recusado", corrigir (dados, 2FA, atividade) e pedir de novo.',
    ],
    links: [
      { label: 'Sobre o Meta Verified para empresas (Central de Ajuda)', url: 'https://pt-br.facebook.com/business/help/308979828303560' },
      { label: 'Página oficial do Meta Verified para empresas', url: 'https://www.facebook.com/business/tools/meta-verified-for-business' },
    ],
  },
  {
    titulo: 'WhatsApp — Meta Verified no app WhatsApp Business',
    resumo:
      'Selo de verificado do número comercial no app WhatsApp Business (não é a API), disponível para pequenas empresas no Brasil '
      + 'por assinatura. A Meta analisa a atividade da conta e pode pedir documentos da empresa.',
    requisitos: [
      'Número cadastrado no app WhatsApp Business (não no WhatsApp comum), com a confirmação em duas etapas (PIN) ligada.',
      'Perfil comercial completo e com os dados oficiais da empresa (nome, endereço, CNPJ, site) — mudar depois pode cancelar a assinatura.',
      'Cartão para a assinatura (preço no app).',
    ],
    passos: [
      'No app WhatsApp Business → Configurações (ou Ferramentas comerciais) → Meta Verified → assinar.',
      'Se a Meta pedir, enviar os documentos da empresa.',
      'Resposta em até 3 dias úteis; registrar aqui "em andamento" com data/protocolo, depois "verificado" ou "recusado".',
      'Aprovado: selo azul no perfil, proteção contra falsificação, suporte e uso em vários aparelhos.',
    ],
    links: [
      { label: 'Sobre o Meta Verified para empresas no WhatsApp', url: 'https://faq.whatsapp.com/3872729742954601' },
      { label: 'Sobre as contas comerciais verificadas (WhatsApp)', url: 'https://faq.whatsapp.com/794517045178057' },
    ],
  },
]
