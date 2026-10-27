<script lang="ts">
// Tela "Números do WhatsApp" (Zap, RF10, Onda 4 — docs/zap/desenho.md §8.4,
// §8.5, §13.1 e §13.3). 27/10/2026. Fica dentro de "Lojas e modo" e só
// aparece para ADMINISTRADOR (decisão do Eduardo de 26/10/2026; a API dá 404
// para quem não é). É aqui que o Eduardo — que não usa terminal — cola na
// produção a CHAVE SECRETA do app e o TOKEN do usuário do sistema:
//   - os campos de segredo são mascarados (`WebkitTextSecurity`), sem
//     preenchimento automático (autocomplete off, 1Password/LastPass fora), e
//     o valor só vai no CORPO do POST — é zerado ao fechar e nunca é relido do
//     servidor (a tela só mostra "Token ok, nunca vence, escopos ok" ou o erro
//     em português);
//   - o código de verificação do webhook e o PIN do número só aparecem sob
//     clique, e cada vez fica no Histórico;
//   - "Inscrever" e "Registrar" falam com a Meta: pedem confirmação. Registrar
//     é UMA tentativa por clique e só aparece no dia da troca, no caso certo;
//   - "Pausar" e "Pausar todos" valem na hora, mesmo com o cadastro desligado.
// O passo a passo (o que copiar de onde na Meta) fica no topo, em português
// simples, sem jargão.
//
// As réguas puras e as rotas ficam aqui (o teste `tests/atendimento-zap-
// numeros.cjs` confere que batem com o backend).

export const ZAP = '/api/atendimento/zap'
const id = (v: string) => encodeURIComponent(v)
export const ROTAS = {
  tela: () => `${ZAP}/numeros`,
  apps: () => `${ZAP}/apps`,
  webhook: (app: string) => `${ZAP}/apps/${id(app)}/webhook`,
  verifyToken: (app: string) => `${ZAP}/apps/${id(app)}/verify-token`,
  segredo: (app: string) => `${ZAP}/apps/${id(app)}/segredo`,
  contas: () => `${ZAP}/contas`,
  token: (conta: string) => `${ZAP}/contas/${id(conta)}/token`,
  numerosDaMeta: (conta: string) => `${ZAP}/contas/${id(conta)}/numeros-da-meta`,
  inscrever: (conta: string) => `${ZAP}/contas/${id(conta)}/inscrever`,
  conferirInscricao: (conta: string) => `${ZAP}/contas/${id(conta)}/conferir-inscricao`,
  numeros: () => `${ZAP}/numeros`,
  numero: (n: string) => `${ZAP}/numeros/${id(n)}`,
  testar: (n: string) => `${ZAP}/numeros/${id(n)}/testar`,
  pausar: (n: string) => `${ZAP}/numeros/${id(n)}/pausar`,
  pausarTodos: () => `${ZAP}/numeros/pausar-todos`,
  registrar: (n: string) => `${ZAP}/numeros/${id(n)}/registrar`,
  pin: (n: string) => `${ZAP}/numeros/${id(n)}/pin`,
  reprocessar: () => `${ZAP}/eventos/reprocessar`,
  apagarPorTelefone: () => `${ZAP}/contatos/apagar-por-telefone`,
}

export type AlertaZap = { codigo: string; nivel: string; frase: string; app_ref?: string | null; conta_ref?: string | null; numero_ref?: string | null }
export type AppZap = {
  id: string; app_id: string; nome: string | null; teste: boolean; ativo: boolean
  ultimo_evento_em: string | null; webhook_url: string; segredo_anterior_ate: string | null
  falhas_de_assinatura: number
}
export type AppInscrito = { id: string; nome: string | null }
export type ContaZap = {
  id: string; app_ref: string; waba_id: string; business_id: string | null; nome: string | null; status: string
  token: { ok: boolean; frase: string; nunca_vence: boolean; expira_em: string | null; conferido_em: string | null; trocado_em: string | null }
  inscrito_em: string | null; inscricao_conferida_em: string | null; nosso_inscrito: boolean | null
  outros_apps: AppInscrito[]; ultimo_erro: string | null; ultimo_erro_em: string | null
}
export type NumeroZap = {
  id: string; conta_ref: string; phone_number_id: string; display_phone_number: string
  verified_name: string | null; nome_status: string | null; nome_na_barra: string | null
  marca_id: string | null; canal_id: string | null; modo: string | null; teste: boolean
  etapa: string; caso_migracao: string | null; envio_ligado: boolean; destinos_teste: string[]
  teto_dia: number | null; teto_efetivo: number; enviados_hoje: number; respostas_mes: number; cobradas_mes: number
  saude_status: string; qualidade: string | null; limite: string | null; conexao: { codigo: string; frase: string }
  ultimo_recebido_em: string | null; ultimo_enviado_em: string | null; ultimo_erro: string | null; ultimo_erro_em: string | null
  tem_pin: boolean; registro: { pode: boolean; motivo: string | null; tentativas_72h: number }
  webhook_outro: string[]; testado_em: string | null; observacao: string | null
}
export type NumeroDaMeta = {
  phone_number_id: string; display_phone_number: string; verified_name: string | null
  nome_status: string | null; qualidade: string | null; teste: boolean; cadastrado: boolean
}
export type TelaZap = {
  chaves: { leitura: boolean; envio: boolean; cadastro: boolean; producao: boolean; lista_de_teste: boolean }
  campos_do_webhook: string[]
  apps: AppZap[]
  contas: ContaZap[]
  numeros: NumeroZap[]
  alertas: AlertaZap[]
  eventos_parados: { total: number; por_resultado: Record<string, number> }
}

// As etapas do "um por vez" (as mesmas de `ZAP_NUMERO_ETAPAS` do backend).
export const ETAPAS: { value: string; label: string; hint: string }[] = [
  { value: 'teste', label: 'Teste', hint: 'número de teste da Meta: só os celulares de teste recebem' },
  { value: 'preparando', label: 'Preparando', hint: 'cadastrado; ainda falta tudo do dia da troca' },
  { value: 'aguardando_duoke', label: 'Aguardando o Duoke', hint: 'esperando as respostas e a liberação do Duoke' },
  { value: 'migrando', label: 'Migrando (dia da troca)', hint: 'o dia da troca: o botão Registrar só aparece aqui' },
  { value: 'ativo', label: 'No DaVinci', hint: 'o número é atendido pelo DaVinci' },
  { value: 'pausado', label: 'Pausado', hint: 'não manda nada (continua recebendo)' },
  { value: 'desligado', label: 'Desligado', hint: 'fora do DaVinci' },
]
// As que mandam mensagem (as mesmas do `zap.ETAPAS_QUE_ENVIAM` do backend).
export const ETAPAS_QUE_ENVIAM = ['teste', 'ativo']
export function etapaInfo(v: string) {
  return ETAPAS.find((e) => e.value === v) || { value: v, label: v, hint: '' }
}
export function podeLigarEnvio(n: Pick<NumeroZap, 'etapa'>): boolean {
  return ETAPAS_QUE_ENVIAM.includes(n.etapa)
}

// O caso do roteiro do dia da troca (§13.3).
export const CASOS: { value: string; label: string }[] = [
  { value: 'A', label: 'A — o número está na API numa conta do Duoke (troca de conta)' },
  { value: 'A2', label: 'A2 — a conta já é nossa; o Duoke só tem acesso (NÃO registrar)' },
  { value: 'B', label: 'B — o número está no aplicativo por QR code' },
]

// A pill "Conexão" (os códigos de `zap_saude.conexao`).
export const CONEXAO: Record<string, { label: string; cls: string }> = {
  conectado: { label: 'Conectado', cls: 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300' },
  sem_eventos: { label: 'Sem mensagens ainda', cls: 'bg-muted text-muted-foreground' },
  token_vencido: { label: 'Token vencido', cls: 'bg-red-500/15 text-red-700 dark:text-red-300' },
  token_sem_permissao: { label: 'Token sem permissão', cls: 'bg-red-500/15 text-red-700 dark:text-red-300' },
  assinatura_falhando: { label: 'Assinatura falhando', cls: 'bg-red-500/15 text-red-700 dark:text-red-300' },
  restrito: { label: 'Restrito pela Meta', cls: 'bg-red-500/15 text-red-700 dark:text-red-300' },
  erro: { label: 'Erro da Meta', cls: 'bg-red-500/15 text-red-700 dark:text-red-300' },
  webhook_outro: { label: 'Indo para outro lugar', cls: 'bg-red-500/15 text-red-700 dark:text-red-300' },
  nao_registrado: { label: 'Não registrado na API', cls: 'bg-red-500/15 text-red-700 dark:text-red-300' },
  webhook_nao_conferido: { label: 'Webhook não conferido', cls: 'bg-amber-500/15 text-amber-800 dark:text-amber-300' },
}

// "Celulares de teste": um por item, separados por vírgula, ponto e vírgula ou
// linha — o espaço é do número ("+55 47 98417-7571" é UM celular). Só dígitos.
export function celularesDeTeste(texto: string): string[] {
  return texto.split(/[,;\n]+/).map((x) => x.replace(/\D/g, '')).filter(Boolean)
}

// O Registrar: no dia da troca (Migrando) e no número de teste (etapa Teste).
export function mostraMotivoDoRegistro(n: Pick<NumeroZap, 'etapa' | 'teste' | 'registro'>): boolean {
  return !n.registro.pode && (n.etapa === 'migrando' || (n.teste && n.etapa === 'teste'))
}
export function conexaoInfo(codigo: string) {
  return CONEXAO[codigo] || { label: codigo, cls: 'bg-muted text-muted-foreground' }
}

export function qualidadeInfo(q: string | null | undefined): { label: string; cls: string } {
  switch ((q || '').toUpperCase()) {
    case 'GREEN': return { label: 'Verde', cls: 'text-emerald-700 dark:text-emerald-300' }
    case 'YELLOW': return { label: 'Amarela', cls: 'text-amber-700 dark:text-amber-300' }
    case 'RED': return { label: 'Vermelha', cls: 'text-red-700 dark:text-red-300' }
    default: return { label: '—', cls: 'text-muted-foreground' }
  }
}

// O nome de exibição na Meta (`name_status`), em português.
export const NOME_STATUS: Record<string, string> = {
  APPROVED: 'aprovado',
  AVAILABLE_WITHOUT_REVIEW: 'liberado sem revisão',
  PENDING_REVIEW: 'em análise na Meta',
  DECLINED: 'recusado pela Meta',
  EXPIRED: 'vencido',
  NONE: 'sem nome',
}
export function nomeStatusTexto(s: string | null | undefined): string {
  return NOME_STATUS[(s || '').toUpperCase()] || (s || '').toLowerCase().replace(/_/g, ' ')
}

// "N de 1.000 grátis" (o contador do mês; o aviso sai em 800 e em 1.000).
export const RESPOSTAS_GRATIS = 1000
export function respostasTexto(n: Pick<NumeroZap, 'respostas_mes'>): string {
  return `${n.respostas_mes.toLocaleString('pt-BR')} de ${RESPOSTAS_GRATIS.toLocaleString('pt-BR')} grátis`
}
// O teto do dia: aviso em 70 % (§6.1).
export function tetoPerto(n: Pick<NumeroZap, 'enviados_hoje' | 'teto_efetivo'>): boolean {
  return n.teto_efetivo > 0 && n.enviados_hoje >= n.teto_efetivo * 0.7
}

// Onde o webhook do número aponta para fora (os `onde` de `zap_cadastro.webhook_em_outro_lugar`).
export const WEBHOOK_OUTRO: Record<string, string> = {
  phone_number: 'o número tem outro endereço',
  whatsapp_business_account: 'a conta tem outro endereço',
  application: 'a URL colada na Meta não é a do DaVinci',
}

// Os atributos de TODO campo de segredo (chave, token, PIN): nada de
// preenchimento automático nem gerenciador de senha.
export const CAMPO_SEGREDO = {
  autocomplete: 'off',
  'data-1p-ignore': '',
  'data-lpignore': 'true',
  'data-form-type': 'other',
  spellcheck: 'false',
} as const
</script>

<script setup lang="ts">
import { Check, Copy, Eye, EyeOff, KeyRound, Loader2, OctagonPause, Plug, Plus, RefreshCw, RotateCcw, Search, ShieldCheck, TriangleAlert, X } from 'lucide-vue-next'
import { erroDaApi, fmtDataHora, haQuanto, useRelogio } from '~/components/AtendimentoPlataforma.vue'

const props = defineProps<{
  // Admin que pode MEXER (ATENDIMENTO_USUARIOS). Sem isso, a tela só mostra.
  podeMexer: boolean
}>()

const { api } = useApi()
const toasts = useToasts()
const agora = useRelogio()

const tela = ref<TelaZap | null>(null)
const carregando = ref(false)
const erro = ref<string | null>(null)
const ocupado = ref<string | null>(null)

const mexe = computed(() => props.podeMexer && !!tela.value?.chaves.cadastro)
const desligadaEVazia = computed(() => !!tela.value && !tela.value.chaves.cadastro && !tela.value.apps.length)

async function carregar() {
  carregando.value = true
  erro.value = null
  try {
    tela.value = await api<TelaZap>(ROTAS.tela())
  } catch (e: any) {
    erro.value = erroDaApi(e, 'Não consegui carregar os números do WhatsApp').texto
  } finally {
    carregando.value = false
  }
}
onMounted(carregar)

function falhou(e: any, padrao: string) {
  const er = erroDaApi(e, padrao)
  toasts.error(er.texto, er.motivos)
}

async function copiar(texto: string, oque: string) {
  try {
    await navigator.clipboard.writeText(texto)
    toasts.success(`${oque} copiado`)
  } catch {
    toasts.warning('Não consegui copiar', 'Selecione o texto e copie com Ctrl+C.')
  }
}

const contasDoApp = (app: AppZap) => (tela.value?.contas || []).filter((c) => c.app_ref === app.id)
const numerosDaConta = (conta: ContaZap) => (tela.value?.numeros || []).filter((n) => n.conta_ref === conta.id)
const nomeDoNumero = (n: NumeroZap) => n.nome_na_barra || n.verified_name || n.display_phone_number
const alertasDe = (campo: 'app_ref' | 'conta_ref' | 'numero_ref', alvo: string) =>
  (tela.value?.alertas || []).filter((a) => a[campo] === alvo)
const alertasGerais = computed(() => (tela.value?.alertas || []).filter((a) => !a.app_ref && !a.conta_ref && !a.numero_ref))
const totais = computed(() => {
  const ns = tela.value?.numeros || []
  return {
    numeros: ns.length,
    conectados: ns.filter((n) => n.conexao.codigo === 'conectado').length,
    enviando: ns.filter((n) => n.envio_ligado).length,
    alertas: (tela.value?.alertas || []).length,
  }
})

// ─── segredos: só no v-model enquanto o modal está aberto ───────────────────
const chaveSecreta = ref('')
const tokenValor = ref('')
const pinValor = ref('')
const verSegredo = ref(false)

type Modal =
  | { tipo: 'app' }
  | { tipo: 'conta'; app: AppZap }
  | { tipo: 'token'; conta: ContaZap }
  | { tipo: 'chave'; app: AppZap }
  | { tipo: 'numero'; numero: NumeroZap }
  | { tipo: 'registrar'; numero: NumeroZap }
const modal = ref<Modal | null>(null)
const modalErro = ref<string | null>(null)
const salvando = ref(false)

const appForm = reactive({ nome: 'DaVinci Zap', app_id: '', teste: false })
const contaForm = reactive({ waba_id: '', business_id: '' })
const motivoChave = ref<'rotina' | 'vazou'>('rotina')
const numeroForm = reactive({ nome_na_barra: '', caso_migracao: '' as string, teto_dia: '' as string | number, destinos: '', observacao: '', apagarPin: false })

function abrir(m: Modal) {
  zerarSegredos()
  modalErro.value = null
  if (m.tipo === 'numero') {
    numeroForm.nome_na_barra = m.numero.nome_na_barra || ''
    numeroForm.caso_migracao = m.numero.caso_migracao || ''
    numeroForm.teto_dia = m.numero.teto_dia ?? ''
    numeroForm.destinos = (m.numero.destinos_teste || []).join(', ')
    numeroForm.observacao = m.numero.observacao || ''
    numeroForm.apagarPin = false
  }
  if (m.tipo === 'conta') {
    contaForm.waba_id = ''
    contaForm.business_id = ''
  }
  modal.value = m
}
function zerarSegredos() {
  chaveSecreta.value = ''
  tokenValor.value = ''
  pinValor.value = ''
  verSegredo.value = false
}
function fecharModal() {
  // Zera o texto do segredo — inclusive quando a pessoa desiste no meio.
  zerarSegredos()
  modalErro.value = null
  modal.value = null
}
const estiloSegredo = computed(() => (verSegredo.value ? undefined : { WebkitTextSecurity: 'disc' }))

// ─── o app ──────────────────────────────────────────────────────────────────
async function salvarApp() {
  if (!appForm.app_id.trim() || !chaveSecreta.value.trim()) {
    modalErro.value = 'Preencha o ID do app e cole a chave secreta.'
    return
  }
  salvando.value = true
  try {
    // A chave vai só no CORPO do POST e não volta nunca.
    await api(ROTAS.apps(), { method: 'POST', body: { nome: appForm.nome, app_id: appForm.app_id.trim(), app_secret: chaveSecreta.value, teste: appForm.teste } })
    fecharModal()
    toasts.success('App cadastrado', 'Agora copie a URL e o código de verificação para a Meta (passo 2).')
    await carregar()
  } catch (e: any) {
    modalErro.value = erroDaApi(e, 'Não consegui cadastrar o app').texto
  } finally {
    salvando.value = false
  }
}

const verifyMostrado = ref<Record<string, string>>({})
async function mostrarVerify(app: AppZap) {
  if (verifyMostrado.value[app.id]) {
    const { [app.id]: _fora, ...resto } = verifyMostrado.value
    verifyMostrado.value = resto
    return
  }
  ocupado.value = `verify:${app.id}`
  try {
    // Cada vez que alguém vê o código, fica no Histórico.
    const r = await api<{ verify_token: string }>(ROTAS.verifyToken(app.id))
    verifyMostrado.value = { ...verifyMostrado.value, [app.id]: r.verify_token }
  } catch (e: any) {
    falhou(e, 'Não consegui mostrar o código')
  } finally {
    ocupado.value = null
  }
}
async function gerarOutroVerify(app: AppZap) {
  if (!confirm('Gerar outro código de verificação?\n\nO código antigo para de valer: depois de gerar, cole o NOVO na Meta (WhatsApp → Configuração → Webhook → Editar).')) return
  ocupado.value = `verify:${app.id}`
  try {
    await api(ROTAS.verifyToken(app.id), { method: 'POST' })
    const { [app.id]: _fora, ...resto } = verifyMostrado.value
    verifyMostrado.value = resto
    toasts.success('Código novo gerado', 'Clique em "Mostrar código de verificação" e cole na Meta.')
  } catch (e: any) {
    falhou(e, 'Não consegui gerar outro código')
  } finally {
    ocupado.value = null
  }
}
async function salvarChave() {
  const m = modal.value
  if (m?.tipo !== 'chave') return
  if (!chaveSecreta.value.trim()) {
    modalErro.value = 'Cole a chave secreta NOVA (Configurações do app → Básico → Chave secreta → Redefinir).'
    return
  }
  salvando.value = true
  try {
    const r = await api<{ anterior_ate: string | null }>(ROTAS.segredo(m.app.id), { method: 'POST', body: { app_secret: chaveSecreta.value, motivo: motivoChave.value } })
    fecharModal()
    toasts.success('Chave secreta trocada', r?.anterior_ate ? `A antiga ainda vale até ${fmtDataHora(r.anterior_ate)}.` : 'A antiga parou de valer agora.')
    await carregar()
  } catch (e: any) {
    modalErro.value = erroDaApi(e, 'Não consegui trocar a chave').texto
  } finally {
    salvando.value = false
  }
}

// ─── a conta (WABA) e o token ───────────────────────────────────────────────
const achados = ref<{ conta: string; numeros: NumeroDaMeta[] } | null>(null)
async function salvarConta() {
  const m = modal.value
  if (m?.tipo !== 'conta') return
  if (!contaForm.waba_id.trim() || !tokenValor.value.trim()) {
    modalErro.value = 'Preencha o ID da conta do WhatsApp e cole o token.'
    return
  }
  salvando.value = true
  try {
    // O token vai só no CORPO; o DaVinci confere na Meta ANTES de gravar.
    const r = await api<{ conta: ContaZap; numeros: NumeroDaMeta[] }>(ROTAS.contas(), {
      method: 'POST',
      body: { app_ref: m.app.id, waba_id: contaForm.waba_id.trim(), business_id: contaForm.business_id.trim() || null, access_token: tokenValor.value },
    })
    fecharModal()
    toasts.success('Conta conferida e gravada', r.conta.token.frase)
    achados.value = { conta: r.conta.id, numeros: r.numeros }
    await carregar()
  } catch (e: any) {
    modalErro.value = erroDaApi(e, 'A Meta não aceitou o token').texto
  } finally {
    salvando.value = false
  }
}
async function salvarToken() {
  const m = modal.value
  if (m?.tipo !== 'token') return
  if (!tokenValor.value.trim()) {
    modalErro.value = 'Cole o token NOVO.'
    return
  }
  salvando.value = true
  try {
    const r = await api<{ lembrete: string }>(ROTAS.token(m.conta.id), { method: 'POST', body: { access_token: tokenValor.value } })
    fecharModal()
    toasts.success('Token trocado', r.lembrete)
    await carregar()
  } catch (e: any) {
    modalErro.value = erroDaApi(e, 'A Meta não aceitou o token novo').texto
  } finally {
    salvando.value = false
  }
}
async function procurarNumeros(conta: ContaZap) {
  ocupado.value = `procurar:${conta.id}`
  try {
    const r = await api<{ numeros: NumeroDaMeta[] }>(ROTAS.numerosDaMeta(conta.id))
    achados.value = { conta: conta.id, numeros: r.numeros }
    if (!r.numeros.length) toasts.info('A Meta não mostrou nenhum número nesta conta')
  } catch (e: any) {
    falhou(e, 'Não consegui procurar os números')
  } finally {
    ocupado.value = null
  }
}
async function cadastrarNumero(conta: string, n: NumeroDaMeta) {
  ocupado.value = `cadastrar:${n.phone_number_id}`
  try {
    await api(ROTAS.numeros(), { method: 'POST', body: { conta_ref: conta, phone_number_id: n.phone_number_id } })
    toasts.success('Número cadastrado', 'Ele nasce em "observar" e com o envio desligado.')
    if (achados.value) achados.value = { ...achados.value, numeros: achados.value.numeros.map((x) => (x.phone_number_id === n.phone_number_id ? { ...x, cadastrado: true } : x)) }
    await carregar()
  } catch (e: any) {
    falhou(e, 'Não consegui cadastrar o número')
  } finally {
    ocupado.value = null
  }
}
const resultadoInscricao = ref<Record<string, string>>({})
async function inscrever(conta: ContaZap) {
  if (!confirm(`Inscrever o DaVinci na conta ${conta.nome || conta.waba_id}?\n\nIsso fala com a Meta: a partir daqui as mensagens desta conta também vêm para o DaVinci. Depois o DaVinci confere se só ele ficou inscrito.`)) return
  ocupado.value = `inscrever:${conta.id}`
  try {
    const r = await api<{ inscrito: boolean; frase: string }>(ROTAS.inscrever(conta.id), { method: 'POST' })
    resultadoInscricao.value = { ...resultadoInscricao.value, [conta.id]: r.frase }
    ;(r.inscrito ? toasts.success : toasts.warning)(r.inscrito ? 'Inscrito' : 'Atenção na inscrição', r.frase)
    await carregar()
  } catch (e: any) {
    falhou(e, 'Não consegui inscrever')
  } finally {
    ocupado.value = null
  }
}
async function conferirInscricao(conta: ContaZap) {
  ocupado.value = `inscrever:${conta.id}`
  try {
    const r = await api<{ inscrito: boolean; frase: string }>(ROTAS.conferirInscricao(conta.id), { method: 'POST' })
    resultadoInscricao.value = { ...resultadoInscricao.value, [conta.id]: r.frase }
    await carregar()
  } catch (e: any) {
    falhou(e, 'Não consegui conferir a inscrição')
  } finally {
    ocupado.value = null
  }
}
function inscricaoTexto(c: ContaZap): { texto: string; ok: boolean } {
  if (c.outros_apps.length) return { texto: `Outro app inscrito: ${c.outros_apps.map((a) => a.nome || a.id).join(', ')}`, ok: false }
  if (c.inscrito_em) return { texto: 'Só o DaVinci está inscrito', ok: true }
  if (c.nosso_inscrito === false) return { texto: 'O DaVinci NÃO está inscrito', ok: false }
  return { texto: 'Inscrição ainda não feita/conferida', ok: false }
}

// ─── o número ───────────────────────────────────────────────────────────────
const resultadoTeste = ref<Record<string, string>>({})
async function testar(n: NumeroZap) {
  ocupado.value = `testar:${n.id}`
  try {
    const r = await api<{ frase: string; webhook_outro: string[] }>(ROTAS.testar(n.id), { method: 'POST' })
    resultadoTeste.value = { ...resultadoTeste.value, [n.id]: r.frase }
    ;(r.webhook_outro.length ? toasts.warning : toasts.success)('Teste da conexão', r.frase)
    await carregar()
  } catch (e: any) {
    const er = erroDaApi(e, 'O teste falhou')
    resultadoTeste.value = { ...resultadoTeste.value, [n.id]: er.texto }
    toasts.error(er.texto, er.motivos)
    await carregar()
  } finally {
    ocupado.value = null
  }
}
async function mudarNumero(n: NumeroZap, corpo: Record<string, unknown>, ok: string) {
  ocupado.value = `numero:${n.id}`
  try {
    await api(ROTAS.numero(n.id), { method: 'PATCH', body: corpo })
    toasts.success(ok)
    await carregar()
    return true
  } catch (e: any) {
    falhou(e, 'Não consegui mudar o número')
    await carregar()
    return false
  } finally {
    ocupado.value = null
  }
}
async function mudarEtapa(n: NumeroZap, ev: Event) {
  const sel = ev.target as HTMLSelectElement
  const nova = sel.value
  if (nova === n.etapa) return
  const e = etapaInfo(nova)
  if (!confirm(`Mudar ${nomeDoNumero(n)} para "${e.label}"?\n\n${e.hint}.`)) {
    sel.value = n.etapa
    return
  }
  if (!(await mudarNumero(n, { etapa: nova }, `${nomeDoNumero(n)}: ${e.label}`))) sel.value = n.etapa
}
async function mudarEnvio(n: NumeroZap, ev: Event) {
  const caixa = ev.target as HTMLInputElement
  const ligar = caixa.checked
  if (ligar) {
    if (!confirm(`LIGAR o envio de ${nomeDoNumero(n)}?\n\nA equipe passa a poder responder este número pelo DaVinci (se as chaves do servidor estiverem ligadas e o modo for Humano). Combine com quem usa o Duoke para o cliente não receber duas respostas.`)) {
      caixa.checked = false
      return
    }
    if (!(await mudarNumero(n, { envio_ligado: true }, `${nomeDoNumero(n)}: envio ligado`))) caixa.checked = false
    return
  }
  // Desligar = o botão "Pausar" (vale na hora, sem confirmação).
  await pausar(n)
}
async function pausar(n: NumeroZap) {
  ocupado.value = `numero:${n.id}`
  try {
    await api(ROTAS.pausar(n.id), { method: 'POST' })
    toasts.success(`${nomeDoNumero(n)}: envio pausado`)
    await carregar()
  } catch (e: any) {
    falhou(e, 'Não consegui pausar')
    // A caixa desmarcada não pode mentir: relê o que o servidor tem (envio ainda ligado).
    await carregar()
  } finally {
    ocupado.value = null
  }
}
async function pausarTodos() {
  if (!confirm('PAUSAR O ENVIO DE TODOS os números do WhatsApp agora?\n\nNinguém consegue mandar mensagem pelo Zap até alguém ligar de novo, número por número. O recebimento continua.')) return
  ocupado.value = 'pausar-todos'
  try {
    const r = await api<{ pausados: number }>(ROTAS.pausarTodos(), { method: 'POST' })
    toasts.success('Todos pausados', `${r.pausados} número(s) pararam de mandar.`)
    await carregar()
  } catch (e: any) {
    falhou(e, 'Não consegui pausar todos')
  } finally {
    ocupado.value = null
  }
}
async function salvarNumero() {
  const m = modal.value
  if (m?.tipo !== 'numero') return
  const corpo: Record<string, unknown> = {
    nome_na_barra: numeroForm.nome_na_barra.trim() || null,
    caso_migracao: numeroForm.caso_migracao || null,
    teto_dia: numeroForm.teto_dia === '' ? null : Number(numeroForm.teto_dia),
    destinos_teste: celularesDeTeste(numeroForm.destinos),
    observacao: numeroForm.observacao.trim() || null,
  }
  // O PIN só vai se a pessoa digitou (ou pediu para apagar); nunca é relido.
  if (pinValor.value.trim()) corpo.pin = pinValor.value.trim()
  else if (numeroForm.apagarPin) corpo.pin = ''
  salvando.value = true
  try {
    await api(ROTAS.numero(m.numero.id), { method: 'PATCH', body: corpo })
    fecharModal()
    toasts.success('Número salvo')
    await carregar()
  } catch (e: any) {
    modalErro.value = erroDaApi(e, 'Não consegui salvar o número').texto
  } finally {
    salvando.value = false
  }
}
const pinMostrado = ref<Record<string, string>>({})
async function mostrarPin(n: NumeroZap) {
  if (pinMostrado.value[n.id]) {
    const { [n.id]: _fora, ...resto } = pinMostrado.value
    pinMostrado.value = resto
    return
  }
  ocupado.value = `pin:${n.id}`
  try {
    // Cada vez que alguém vê o PIN, fica no Histórico.
    const r = await api<{ pin: string | null }>(ROTAS.pin(n.id))
    if (r.pin) pinMostrado.value = { ...pinMostrado.value, [n.id]: r.pin }
  } catch (e: any) {
    falhou(e, 'Não consegui mostrar o PIN')
  } finally {
    ocupado.value = null
  }
}
async function registrar() {
  const m = modal.value
  if (m?.tipo !== 'registrar') return
  salvando.value = true
  try {
    const r = await api<{ ok: boolean; frase: string }>(ROTAS.registrar(m.numero.id), { method: 'POST', body: pinValor.value.trim() ? { pin: pinValor.value.trim() } : {} })
    fecharModal()
    ;(r.ok ? toasts.success : toasts.warning)('Registrar', r.frase)
    await carregar()
  } catch (e: any) {
    modalErro.value = erroDaApi(e, 'O registro não deu certo').texto
    await carregar()
  } finally {
    salvando.value = false
  }
}

// ─── LGPD: o pedido de exclusão pelo telefone (§10.5) ─────────────────────────
const telefoneExclusao = ref('')
async function apagarPorTelefone() {
  const tel = telefoneExclusao.value.trim()
  if (tel.replace(/\D/g, '').length < 10) {
    toasts.warning('Telefone incompleto', 'Digite com DDD, por exemplo 55 11 98765-4321.')
    return
  }
  if (!confirm(`APAGAR os dados do cliente ${tel} no WhatsApp?\n\nSomem as mensagens (viram "[apagado a pedido]"), fotos e áudios, o nome e o telefone — em TODOS os números da loja com que ele falou — e ele não recebe mais nada pelo DaVinci. Não dá para desfazer.`)) return
  ocupado.value = 'apagar-telefone'
  try {
    const r = await api<{ contatos: number; mensagens: number; midias: number }>(ROTAS.apagarPorTelefone(), { method: 'POST', body: { telefone: tel } })
    telefoneExclusao.value = ''
    toasts.success('Dados apagados', `${r.contatos} contato(s), ${r.mensagens} mensagem(ns) e ${r.midias} mídia(s).`)
    await carregar()
  } catch (e: any) {
    falhou(e, 'Não consegui apagar')
  } finally {
    ocupado.value = null
  }
}

// ─── eventos parados ────────────────────────────────────────────────────────
async function reprocessar() {
  ocupado.value = 'reprocessar'
  try {
    const r = await api<{ reabertos: number }>(ROTAS.reprocessar(), { method: 'POST' })
    toasts.success('Avisos de volta à fila', `${r.reabertos} aviso(s) serão lidos de novo em até 1 minuto.`)
    await carregar()
  } catch (e: any) {
    falhou(e, 'Não consegui reprocessar')
  } finally {
    ocupado.value = null
  }
}
</script>

<template>
  <!-- Tudo nasce desligado: sem a chave do cadastro e sem nada cadastrado, só
       uma linha (a tela inteira aparece quando a publicação ligar a chave). -->
  <div v-if="desligadaEVazia" class="flex items-center gap-2 rounded-lg border border-dashed px-3 py-2 text-xs text-muted-foreground">
    <span class="inline-flex size-5 items-center justify-center rounded-full bg-[#25D366]/60 text-white" aria-hidden="true"><Plug class="size-3" /></span>
    Números do WhatsApp: desligado no servidor. A tela de cadastro aparece quando ligarmos (ATENDIMENTO_ZAP_CADASTRO_ATIVO), com o seu OK.
  </div>
  <section v-else class="space-y-3 rounded-lg border bg-card p-3" aria-label="Números do WhatsApp">
    <div class="flex flex-wrap items-center gap-2">
      <span class="inline-flex size-6 items-center justify-center rounded-full bg-[#25D366] text-white" aria-hidden="true"><Plug class="size-3.5" /></span>
      <h2 class="text-sm font-semibold">Números do WhatsApp</h2>
      <span class="text-xs text-muted-foreground">só administrador vê esta parte</span>
      <div class="ml-auto flex items-center gap-2">
        <Button size="sm" variant="outline" :disabled="carregando" @click="carregar">
          <RotateCcw class="mr-1.5 size-4" :class="{ 'animate-spin': carregando }" /> atualizar
        </Button>
        <Button size="sm" variant="destructive" :disabled="!podeMexer || ocupado === 'pausar-todos' || !totais.enviando" title="emergência: todos os números param de mandar na hora (o recebimento continua)" @click="pausarTodos">
          <OctagonPause class="mr-1.5 size-4" /> Pausar todos
        </Button>
      </div>
    </div>

    <div v-if="erro" class="rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-600 dark:text-red-400">
      {{ erro }} <button type="button" class="ml-2 text-xs underline" @click="carregar">tentar de novo</button>
    </div>

    <template v-if="tela">
      <!-- As chaves do servidor (quem liga é a publicação, com o OK do Eduardo) -->
      <div class="flex flex-wrap gap-1.5 text-[11px]">
        <span class="rounded px-1.5 py-0.5" :class="tela.chaves.leitura ? 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300' : 'bg-muted text-muted-foreground'">Receber: {{ tela.chaves.leitura ? 'ligado' : 'desligado' }}</span>
        <span class="rounded px-1.5 py-0.5" :class="tela.chaves.envio ? 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300' : 'bg-muted text-muted-foreground'">Responder: {{ tela.chaves.envio ? 'ligado' : 'desligado' }}</span>
        <span class="rounded px-1.5 py-0.5" :class="tela.chaves.cadastro ? 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300' : 'bg-amber-500/15 text-amber-700 dark:text-amber-300'">Cadastro: {{ tela.chaves.cadastro ? 'ligado' : 'desligado (a tela só mostra)' }}</span>
        <span v-if="!tela.chaves.producao" class="rounded bg-sky-500/15 px-1.5 py-0.5 text-sky-700 dark:text-sky-300">computador de teste: só entra o app e o número de TESTE</span>
        <span v-if="tela.chaves.lista_de_teste" class="rounded bg-sky-500/15 px-1.5 py-0.5 text-sky-700 dark:text-sky-300">modo teste: só os celulares de teste recebem</span>
      </div>

      <div class="grid grid-cols-2 gap-2 lg:grid-cols-4">
        <StatCard label="Números" :value="totais.numeros" compact />
        <StatCard label="Conectados" :value="totais.conectados" :icon="ShieldCheck" tone="success" compact />
        <StatCard label="Com envio ligado" :value="totais.enviando" compact />
        <StatCard label="Alertas" :value="totais.alertas" :icon="TriangleAlert" tone="danger" compact />
      </div>

      <!-- Os alertas gerais (a fila parada etc.) -->
      <ul v-if="alertasGerais.length" class="space-y-1">
        <li v-for="a in alertasGerais" :key="a.codigo" class="rounded-md border px-2 py-1 text-xs" :class="a.nivel === 'erro' ? 'border-red-500/40 bg-red-500/10 text-red-700 dark:text-red-300' : 'border-amber-500/40 bg-amber-500/10 text-amber-800 dark:text-amber-300'">
          <TriangleAlert class="mr-1 inline size-3.5" />{{ a.frase }}
        </li>
      </ul>

      <!-- O passo a passo (§13.1), em português simples -->
      <details class="rounded-md border bg-background px-3 py-2 text-xs" :open="!tela.apps.length">
        <summary class="cursor-pointer font-medium">Passo a passo: como ligar um número (o que copiar de onde na Meta)</summary>
        <ol class="mt-2 list-decimal space-y-1.5 pl-5 text-muted-foreground">
          <li><strong class="text-foreground">Cadastrar o app.</strong> Em developers.facebook.com, abra o app <strong>"DaVinci Zap"</strong> (o do Gerenciador dos números, não o do Instagram) → <em>Configurações do app → Básico</em>. Copie o <strong>ID do app</strong> e a <strong>Chave secreta do app</strong> (clique em "Mostrar"). Aqui, clique em <strong>Cadastrar app</strong> e cole os dois. A chave fica escondida e nunca mais aparece.</li>
          <li><strong class="text-foreground">Ligar o webhook na Meta.</strong> No app da Meta, vá em <em>WhatsApp → Configuração → Webhook → Editar</em>. Cole a <strong>URL do webhook</strong> (botão Copiar no app, aqui embaixo) e o <strong>código de verificação</strong> (botão Mostrar código de verificação). Clique em "Verificar e salvar". Depois, em "Campos do webhook", marque <strong>todos estes</strong>: <span class="font-mono">{{ tela.campos_do_webhook.join(', ') }}</span>.</li>
          <li><strong class="text-foreground">Gerar o token.</strong> Em <em>Configurações do negócio → Usuários do sistema</em>, abra o usuário <strong>"DaVinci Zap"</strong> (do tipo <strong>funcionário</strong>, não administrador). Em "Atribuir ativos", dê a ele o app e a conta do WhatsApp do número. Clique em <strong>Gerar token</strong>: escolha o app "DaVinci Zap", validade <strong>Nunca</strong> e as permissões <span class="font-mono">whatsapp_business_messaging</span> e <span class="font-mono">whatsapp_business_management</span>. Copie o token.</li>
          <li><strong class="text-foreground">Cadastrar a conta do WhatsApp.</strong> No app aqui embaixo, clique em <strong>Cadastrar conta do WhatsApp</strong>. Cole o <strong>ID da conta do WhatsApp</strong> (em <em>WhatsApp → Configuração da API</em>, "ID da conta do WhatsApp Business") e o <strong>token</strong>. O DaVinci confere na Meta e diz "Token ok, nunca vence, escopos ok" — ou o que está errado.</li>
          <li><strong class="text-foreground">Escolher o número.</strong> Aparece a lista dos números da conta. Clique em <strong>Cadastrar este número</strong>. Ele entra só lendo (observar) e com o envio desligado.</li>
          <li><strong class="text-foreground">Inscrever.</strong> Na conta, clique em <strong>Inscrever</strong>. O DaVinci passa a receber as mensagens e confere se só ele ficou inscrito. Se aparecer outro app (o do Duoke), peça para eles removerem.</li>
          <li><strong class="text-foreground">Testar.</strong> No número, clique em <strong>Testar conexão</strong>: tem de dizer que as mensagens vêm para o DaVinci. Mande um "oi" do seu celular e veja chegar no Atendimento.</li>
          <li><strong class="text-foreground">No dia da troca de um número real</strong> (um por vez, com o roteiro e o "vai/não vai"): mude a etapa para <strong>Migrando</strong>, marque o caso (A, A2 ou B) e o PIN em <strong>Editar</strong>. O botão <strong>Registrar</strong> só aparece nos casos A e B (e no número de teste, na etapa Teste, se a Meta disser que ele não está registrado). Só depois do teste de 5 minutos: etapa <strong>No DaVinci</strong>, modo Humano e envio ligado.</li>
        </ol>
        <p class="mt-2 text-muted-foreground">Se a chave secreta ou o token vazarem: use <strong>Trocar chave secreta</strong> (escolha "vazou") ou <strong>Trocar token</strong>, e depois revogue o antigo na Meta.</p>
      </details>

      <div class="flex flex-wrap items-center gap-2">
        <Button size="sm" :disabled="!mexe" :title="mexe ? 'o app da Meta: ID e chave secreta' : 'o cadastro está desligado no servidor'" @click="abrir({ tipo: 'app' })">
          <Plus class="mr-1.5 size-4" /> Cadastrar app
        </Button>
        <span v-if="!tela.chaves.cadastro" class="text-[11px] text-amber-700 dark:text-amber-300">O cadastro está desligado no servidor: peça para ligar quando for cadastrar.</span>
        <span v-if="tela.eventos_parados.total" class="ml-auto inline-flex items-center gap-2 text-[11px] text-muted-foreground">
          {{ tela.eventos_parados.total }} aviso(s) da Meta parados (número ou conta que ainda não estavam cadastrados)
          <Button size="sm" variant="outline" class="h-7 text-xs" :disabled="!mexe || ocupado === 'reprocessar'" @click="reprocessar">Reprocessar ignorados</Button>
        </span>
      </div>

      <div v-if="!tela.apps.length" class="rounded-md border border-dashed px-3 py-6 text-center text-sm text-muted-foreground">Nenhum app cadastrado ainda — comece pelo passo 1 acima.</div>

      <!-- Cada app, as contas e os números -->
      <article v-for="app in tela.apps" :key="app.id" class="space-y-2 rounded-md border p-2">
        <div class="flex flex-wrap items-center gap-2 text-xs">
          <span class="font-semibold">{{ app.nome || 'App da Meta' }}</span>
          <span class="rounded px-1.5 py-px text-[10px]" :class="app.teste ? 'bg-sky-500/15 text-sky-700 dark:text-sky-300' : 'bg-violet-500/15 text-violet-700 dark:text-violet-300'">{{ app.teste ? 'app de teste' : 'app de produção' }}</span>
          <span class="text-muted-foreground">ID do app {{ app.app_id }}</span>
          <span class="text-muted-foreground" :title="fmtDataHora(app.ultimo_evento_em)">· último aviso da Meta: {{ app.ultimo_evento_em ? haQuanto(app.ultimo_evento_em, agora) : 'nenhum ainda' }}</span>
          <span v-if="app.segredo_anterior_ate" class="text-amber-700 dark:text-amber-300">· a chave antiga ainda vale até {{ fmtDataHora(app.segredo_anterior_ate) }}</span>
        </div>
        <ul v-if="alertasDe('app_ref', app.id).length" class="space-y-1">
          <li v-for="a in alertasDe('app_ref', app.id)" :key="a.codigo" class="rounded border border-red-500/40 bg-red-500/10 px-2 py-1 text-xs text-red-700 dark:text-red-300">{{ a.frase }}</li>
        </ul>
        <div class="flex flex-wrap items-center gap-2 text-xs">
          <span class="text-muted-foreground">URL do webhook (cole na Meta):</span>
          <input :value="app.webhook_url" readonly class="h-7 min-w-0 flex-1 rounded border bg-muted/40 px-2 font-mono text-[11px]" aria-label="URL do webhook" @focus="($event.target as HTMLInputElement).select()" />
          <Button size="sm" variant="outline" class="h-7 text-xs" @click="copiar(app.webhook_url, 'URL')"><Copy class="mr-1 size-3.5" />Copiar</Button>
        </div>
        <div class="flex flex-wrap items-center gap-2 text-xs">
          <span class="text-muted-foreground">Código de verificação:</span>
          <template v-if="verifyMostrado[app.id]">
            <input :value="verifyMostrado[app.id]" readonly class="h-7 min-w-0 flex-1 rounded border bg-muted/40 px-2 font-mono text-[11px]" aria-label="código de verificação" />
            <Button size="sm" variant="outline" class="h-7 text-xs" @click="copiar(verifyMostrado[app.id], 'Código')"><Copy class="mr-1 size-3.5" />Copiar</Button>
          </template>
          <Button size="sm" variant="outline" class="h-7 text-xs" :disabled="ocupado === `verify:${app.id}`" title="mostra o código (fica registrado no Histórico)" @click="mostrarVerify(app)">
            <EyeOff v-if="verifyMostrado[app.id]" class="mr-1 size-3.5" /><Eye v-else class="mr-1 size-3.5" />{{ verifyMostrado[app.id] ? 'Esconder' : 'Mostrar código de verificação' }}
          </Button>
          <Button size="sm" variant="ghost" class="h-7 text-xs" :disabled="!mexe" @click="gerarOutroVerify(app)">Gerar outro</Button>
          <Button size="sm" variant="ghost" class="h-7 text-xs" :disabled="!mexe" @click="abrir({ tipo: 'chave', app })"><KeyRound class="mr-1 size-3.5" />Trocar chave secreta</Button>
          <Button size="sm" class="ml-auto h-7 text-xs" :disabled="!mexe" @click="abrir({ tipo: 'conta', app })"><Plus class="mr-1 size-3.5" />Cadastrar conta do WhatsApp</Button>
        </div>

        <div v-for="conta in contasDoApp(app)" :key="conta.id" class="space-y-2 rounded border bg-background p-2">
          <div class="flex flex-wrap items-center gap-2 text-xs">
            <span class="font-medium">{{ conta.nome || 'Conta do WhatsApp' }}</span>
            <span class="text-muted-foreground">ID da conta {{ conta.waba_id }}</span>
            <span class="rounded px-1.5 py-px text-[11px]" :class="conta.token.ok ? 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300' : 'bg-red-500/15 text-red-700 dark:text-red-300'">{{ conta.token.frase }}</span>
            <span class="rounded px-1.5 py-px text-[11px]" :class="inscricaoTexto(conta).ok ? 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300' : 'bg-amber-500/15 text-amber-800 dark:text-amber-300'">{{ inscricaoTexto(conta).texto }}</span>
          </div>
          <ul v-if="alertasDe('conta_ref', conta.id).length" class="space-y-1">
            <li v-for="a in alertasDe('conta_ref', conta.id)" :key="a.codigo" class="rounded border px-2 py-1 text-xs" :class="a.nivel === 'erro' ? 'border-red-500/40 bg-red-500/10 text-red-700 dark:text-red-300' : 'border-amber-500/40 bg-amber-500/10 text-amber-800 dark:text-amber-300'">{{ a.frase }}</li>
          </ul>
          <p v-if="resultadoInscricao[conta.id]" class="text-[11px] text-muted-foreground">{{ resultadoInscricao[conta.id] }}</p>
          <div class="flex flex-wrap gap-1.5">
            <Button size="sm" variant="outline" class="h-7 text-xs" :disabled="!mexe || ocupado === `inscrever:${conta.id}`" title="fala com a Meta: o DaVinci passa a receber as mensagens desta conta" @click="inscrever(conta)">
              <Loader2 v-if="ocupado === `inscrever:${conta.id}`" class="mr-1 size-3.5 animate-spin" />Inscrever
            </Button>
            <Button size="sm" variant="ghost" class="h-7 text-xs" :disabled="!mexe || ocupado === `inscrever:${conta.id}`" title="só confere na Meta quem está inscrito (não muda nada)" @click="conferirInscricao(conta)">Conferir inscrição</Button>
            <Button size="sm" variant="ghost" class="h-7 text-xs" :disabled="!mexe || ocupado === `procurar:${conta.id}`" @click="procurarNumeros(conta)"><Search class="mr-1 size-3.5" />Procurar números</Button>
            <Button size="sm" variant="ghost" class="h-7 text-xs" :disabled="!mexe" @click="abrir({ tipo: 'token', conta })"><KeyRound class="mr-1 size-3.5" />Trocar token</Button>
          </div>

          <!-- Os números que a Meta mostrou (só GET) -->
          <div v-if="achados && achados.conta === conta.id" class="rounded border border-dashed p-2">
            <div class="mb-1 flex items-center text-[11px] font-medium">
              Números desta conta na Meta
              <button type="button" class="ml-auto text-muted-foreground hover:text-foreground" aria-label="fechar" @click="achados = null"><X class="size-3.5" /></button>
            </div>
            <div v-for="n in achados.numeros" :key="n.phone_number_id" class="flex flex-wrap items-center gap-2 py-0.5 text-xs">
              <span class="font-medium">{{ n.display_phone_number }}</span>
              <span class="text-muted-foreground">{{ n.verified_name || '' }}{{ n.teste ? ' · número de teste da Meta' : '' }}</span>
              <span v-if="n.cadastrado" class="ml-auto inline-flex items-center gap-1 text-emerald-700 dark:text-emerald-300"><Check class="size-3.5" />já cadastrado</span>
              <Button v-else size="sm" class="ml-auto h-7 text-xs" :disabled="!mexe || ocupado === `cadastrar:${n.phone_number_id}`" @click="cadastrarNumero(conta.id, n)">Cadastrar este número</Button>
            </div>
            <div v-if="!achados.numeros.length" class="text-xs text-muted-foreground">Nenhum número nesta conta.</div>
          </div>

          <div v-if="numerosDaConta(conta).length" class="overflow-x-auto">
            <table class="w-full min-w-[1080px] text-xs">
              <thead class="bg-muted/40 text-[11px] text-muted-foreground">
                <tr class="text-left">
                  <th class="px-2 py-1.5 font-semibold">Número</th>
                  <th class="px-2 py-1.5 font-semibold">Etapa</th>
                  <th class="px-2 py-1.5 font-semibold">Conexão</th>
                  <th class="px-2 py-1.5 font-semibold">Último recebido</th>
                  <th class="px-2 py-1.5 font-semibold">Último erro</th>
                  <th class="px-2 py-1.5 font-semibold">Qualidade</th>
                  <th class="px-2 py-1.5 font-semibold" title="respostas do mês (as 1.000 primeiras são grátis; aviso em 800)">Respostas no mês</th>
                  <th class="px-2 py-1.5 font-semibold" title="mensagens mandadas hoje / teto do dia (aviso em 70%)">Hoje</th>
                  <th class="px-2 py-1.5 font-semibold">Envio</th>
                  <th class="px-2 py-1.5 font-semibold">Modo</th>
                  <th class="px-2 py-1.5 font-semibold">Ações</th>
                </tr>
              </thead>
              <tbody class="divide-y">
                <template v-for="n in numerosDaConta(conta)" :key="n.id">
                  <tr class="align-top">
                    <td class="px-2 py-1.5">
                      <div class="font-medium">{{ n.display_phone_number }}</div>
                      <div class="text-[11px] text-muted-foreground">{{ nomeDoNumero(n) }}<span v-if="n.nome_status"> · nome {{ nomeStatusTexto(n.nome_status) }}</span></div>
                    </td>
                    <td class="px-2 py-1.5">
                      <select :value="n.etapa" :disabled="!mexe || ocupado === `numero:${n.id}`" class="h-7 rounded-md border bg-background px-1.5 text-xs disabled:opacity-60" :aria-label="`etapa de ${nomeDoNumero(n)}`" @change="mudarEtapa(n, $event)">
                        <option v-for="e in ETAPAS" :key="e.value" :value="e.value" :title="e.hint">{{ e.label }}</option>
                      </select>
                      <div v-if="n.caso_migracao" class="mt-0.5 text-[10px] text-muted-foreground">caso {{ n.caso_migracao }}</div>
                    </td>
                    <td class="px-2 py-1.5">
                      <span class="rounded px-1.5 py-0.5 text-[11px] font-medium" :class="conexaoInfo(n.conexao.codigo).cls" :title="n.conexao.frase">{{ conexaoInfo(n.conexao.codigo).label }}</span>
                      <div v-for="o in n.webhook_outro" :key="o" class="mt-0.5 text-[10px] text-red-700 dark:text-red-300">{{ WEBHOOK_OUTRO[o] || o }}</div>
                    </td>
                    <td class="whitespace-nowrap px-2 py-1.5 text-muted-foreground" :title="fmtDataHora(n.ultimo_recebido_em)">{{ n.ultimo_recebido_em ? haQuanto(n.ultimo_recebido_em, agora) : 'nunca' }}</td>
                    <td class="max-w-[220px] px-2 py-1.5"><div v-if="n.ultimo_erro" class="line-clamp-2 text-red-600 dark:text-red-400" :title="n.ultimo_erro">{{ n.ultimo_erro }}</div><span v-else class="text-muted-foreground">—</span></td>
                    <td class="px-2 py-1.5" :class="qualidadeInfo(n.qualidade).cls">{{ qualidadeInfo(n.qualidade).label }}</td>
                    <td class="whitespace-nowrap px-2 py-1.5" :class="n.respostas_mes >= 800 ? 'text-amber-700 dark:text-amber-300' : ''">{{ respostasTexto(n) }}</td>
                    <td class="whitespace-nowrap px-2 py-1.5" :class="tetoPerto(n) ? 'font-medium text-amber-700 dark:text-amber-300' : ''">{{ n.enviados_hoje }} de {{ n.teto_efetivo }}</td>
                    <td class="px-2 py-1.5">
                      <label class="inline-flex items-center gap-1.5" :title="podeLigarEnvio(n) ? 'ligar/pausar o envio deste número' : 'este número não manda nesta etapa'">
                        <input type="checkbox" :checked="n.envio_ligado" :disabled="!podeMexer || ocupado === `numero:${n.id}` || (!n.envio_ligado && (!mexe || !podeLigarEnvio(n)))" :aria-label="`envio de ${nomeDoNumero(n)}`" @change="mudarEnvio(n, $event)" />
                        <span :class="n.envio_ligado ? 'font-medium text-emerald-700 dark:text-emerald-300' : 'text-muted-foreground'">{{ n.envio_ligado ? 'ligado' : 'pausado' }}</span>
                      </label>
                    </td>
                    <td class="px-2 py-1.5 text-muted-foreground" title="o modo se troca na tabela das caixas, abaixo">{{ n.modo === 'humano' ? 'Humano' : n.modo === 'observar' ? 'Observar' : (n.modo || '—') }}</td>
                    <td class="px-2 py-1.5">
                      <div class="flex flex-wrap gap-1">
                        <Button size="sm" variant="outline" class="h-7 text-xs" :disabled="!mexe || ocupado === `testar:${n.id}`" @click="testar(n)">
                          <Loader2 v-if="ocupado === `testar:${n.id}`" class="mr-1 size-3.5 animate-spin" /><RefreshCw v-else class="mr-1 size-3.5" />Testar conexão
                        </Button>
                        <Button v-if="n.envio_ligado" size="sm" variant="outline" class="h-7 text-xs" :disabled="!podeMexer" @click="pausar(n)">Pausar envio</Button>
                        <Button size="sm" variant="ghost" class="h-7 text-xs" :disabled="!mexe" @click="abrir({ tipo: 'numero', numero: n })">Editar</Button>
                        <Button v-if="n.registro.pode" size="sm" variant="ghost" class="h-7 text-xs text-amber-700 dark:text-amber-300" :disabled="!mexe" @click="abrir({ tipo: 'registrar', numero: n })">Registrar</Button>
                        <Button v-if="n.tem_pin" size="sm" variant="ghost" class="h-7 text-xs" :disabled="ocupado === `pin:${n.id}`" title="mostra o PIN (fica registrado no Histórico)" @click="mostrarPin(n)">{{ pinMostrado[n.id] ? `PIN ${pinMostrado[n.id]} · esconder` : 'Mostrar PIN' }}</Button>
                      </div>
                      <div v-if="mostraMotivoDoRegistro(n)" class="mt-0.5 text-[10px] text-muted-foreground">Registrar: {{ n.registro.motivo }}</div>
                    </td>
                  </tr>
                  <tr v-if="alertasDe('numero_ref', n.id).length || resultadoTeste[n.id]">
                    <td colspan="11" class="px-2 pb-1.5">
                      <p v-if="resultadoTeste[n.id]" class="text-[11px] text-muted-foreground">Teste: {{ resultadoTeste[n.id] }}</p>
                      <ul class="space-y-0.5">
                        <li v-for="a in alertasDe('numero_ref', n.id)" :key="a.codigo" class="rounded border px-2 py-0.5 text-[11px]" :class="a.nivel === 'erro' ? 'border-red-500/40 bg-red-500/10 text-red-700 dark:text-red-300' : 'border-amber-500/40 bg-amber-500/10 text-amber-800 dark:text-amber-300'">{{ a.frase }}</li>
                      </ul>
                    </td>
                  </tr>
                </template>
              </tbody>
            </table>
          </div>
          <div v-else class="text-[11px] text-muted-foreground">Nenhum número cadastrado nesta conta — use Procurar números.</div>
        </div>
      </article>
    </template>
    <div v-else-if="carregando" class="py-6 text-center text-sm text-muted-foreground"><Loader2 class="mr-1.5 inline size-4 animate-spin" />carregando…</div>

    <!-- LGPD (§10.5): o cliente pediu para apagar os dados (e-mail da página /exclusao-de-dados). -->
    <details v-if="tela" class="rounded-md border bg-background px-3 py-2 text-xs">
      <summary class="cursor-pointer font-medium">Pedido de exclusão de dados (LGPD)</summary>
      <p class="mt-1 text-muted-foreground">O cliente pediu para apagar os dados dele? Digite o telefone (com DDD). Somem as mensagens, fotos, áudios, nome e telefone em todos os números da loja, e ele não recebe mais nada pelo DaVinci.</p>
      <div class="mt-2 flex flex-wrap items-center gap-2">
        <Input v-model="telefoneExclusao" class="h-8 w-56 font-mono" inputmode="tel" autocomplete="off" placeholder="55 11 98765-4321" />
        <Button size="sm" variant="destructive" class="h-8 text-xs" :disabled="!podeMexer || ocupado === 'apagar-telefone' || !telefoneExclusao.trim()" @click="apagarPorTelefone">Apagar dados do cliente</Button>
      </div>
    </details>

    <!-- Os modais (um por vez). Segredo: mascarado, sem preenchimento automático, zerado ao fechar. -->
    <div v-if="modal" class="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 p-4" @click.self="!salvando && fecharModal()">
      <div class="max-h-[90vh] w-full max-w-lg space-y-3 overflow-auto rounded-lg border bg-background p-5" role="dialog" aria-modal="true">
        <div class="flex items-center gap-2">
          <h3 class="text-base font-semibold">
            {{ modal.tipo === 'app' ? 'Cadastrar app da Meta' : modal.tipo === 'conta' ? 'Cadastrar conta do WhatsApp' : modal.tipo === 'token' ? 'Trocar token' : modal.tipo === 'chave' ? 'Trocar chave secreta' : modal.tipo === 'registrar' ? 'Registrar o número na Meta' : 'Editar número' }}
          </h3>
          <Button class="ml-auto" size="sm" variant="ghost" :disabled="salvando" aria-label="fechar" @click="fecharModal"><X class="size-4" /></Button>
        </div>

        <template v-if="modal.tipo === 'app'">
          <p class="text-[11px] text-muted-foreground">Copie de developers.facebook.com → app "DaVinci Zap" → <strong>Configurações do app → Básico</strong>.</p>
          <label class="block text-xs">Nome (para você reconhecer)
            <Input v-model="appForm.nome" class="mt-1" maxlength="120" />
          </label>
          <label class="block text-xs">ID do app
            <Input v-model="appForm.app_id" class="mt-1 font-mono" inputmode="numeric" autocomplete="off" placeholder="só números" />
          </label>
          <label v-if="tela?.chaves.producao" class="flex items-center gap-2 text-xs"><input v-model="appForm.teste" type="checkbox" /> este é o app de TESTE da Meta ("DaVinci Zap Teste": só cadastra o número de teste)</label>
        </template>
        <template v-else-if="modal.tipo === 'conta'">
          <p class="text-[11px] text-muted-foreground">O ID da conta fica em <strong>WhatsApp → Configuração da API</strong>. O token vem de <strong>Configurações do negócio → Usuários do sistema → "DaVinci Zap" → Gerar token</strong> (validade Nunca).</p>
          <label class="block text-xs">ID da conta do WhatsApp
            <Input v-model="contaForm.waba_id" class="mt-1 font-mono" inputmode="numeric" autocomplete="off" placeholder="só números" />
          </label>
          <label class="block text-xs">ID do portfólio (opcional)
            <Input v-model="contaForm.business_id" class="mt-1 font-mono" inputmode="numeric" autocomplete="off" placeholder="só números" />
          </label>
        </template>
        <template v-else-if="modal.tipo === 'token'">
          <p class="text-[11px] text-muted-foreground">Gere o token NOVO na Meta (Usuários do sistema → "DaVinci Zap" → Gerar token, validade Nunca). O DaVinci confere antes de trocar; depois, <strong>revogue o antigo</strong> na Meta.</p>
        </template>
        <template v-else-if="modal.tipo === 'chave'">
          <p class="text-[11px] text-muted-foreground">Na Meta: Configurações do app → Básico → Chave secreta → <strong>Redefinir</strong>. Cole a nova aqui logo em seguida.</p>
          <div class="space-y-1 text-xs">
            <label class="flex items-center gap-2"><input v-model="motivoChave" type="radio" value="rotina" /> Troca de rotina (a antiga ainda vale por 1 hora)</label>
            <label class="flex items-center gap-2"><input v-model="motivoChave" type="radio" value="vazou" /> A chave vazou (a antiga para de valer em até 1 minuto)</label>
          </div>
        </template>
        <template v-else-if="modal.tipo === 'numero'">
          <p class="text-[11px] text-muted-foreground">{{ modal.numero.display_phone_number }} · {{ nomeDoNumero(modal.numero) }}</p>
          <label class="block text-xs">Nome na barra
            <Input v-model="numeroForm.nome_na_barra" class="mt-1" maxlength="120" placeholder="ex.: Charlots · WhatsApp" />
          </label>
          <label class="block text-xs">Caso da migração (o roteiro do dia da troca)
            <select v-model="numeroForm.caso_migracao" class="mt-1 h-9 w-full rounded-md border bg-background px-2 text-xs">
              <option value="">ainda não sei</option>
              <option v-for="c in CASOS" :key="c.value" :value="c.value">{{ c.label }}</option>
            </select>
          </label>
          <label class="block text-xs">Teto de mensagens por dia (vazio = o padrão; use o volume medido no Duoke, com folga)
            <Input v-model="numeroForm.teto_dia" class="mt-1" type="number" min="0" />
          </label>
          <label class="block text-xs">Celulares de teste deste número (com 55 e DDD, separados por vírgula)
            <Input v-model="numeroForm.destinos" class="mt-1 font-mono" autocomplete="off" />
          </label>
          <label class="block text-xs">Observação
            <Input v-model="numeroForm.observacao" class="mt-1" maxlength="2000" />
          </label>
          <p class="text-[11px] text-muted-foreground">PIN de 6 números (a verificação em 2 etapas do número). Fica guardado escondido; para ver, use "Mostrar PIN". {{ modal.numero.tem_pin ? 'Já existe um PIN guardado: deixe vazio para manter.' : '' }}</p>
          <label v-if="modal.numero.tem_pin" class="flex items-center gap-2 text-xs"><input v-model="numeroForm.apagarPin" type="checkbox" /> apagar o PIN guardado</label>
        </template>
        <template v-else-if="modal.tipo === 'registrar'">
          <p class="rounded border border-amber-500/40 bg-amber-500/10 px-2 py-1 text-xs text-amber-800 dark:text-amber-300">
            UMA tentativa por clique. A Meta bloqueia o número por 72 h depois de tentativas demais, e PIN errado pode travar a conta. Se der "fale com o suporte", PARE e fale com o suporte da Meta.
            Tentativas nas últimas 72 h: {{ modal.numero.registro.tentativas_72h }} de 3.
          </p>
          <p class="text-[11px] text-muted-foreground">Deixe vazio para usar o PIN guardado.</p>
        </template>

        <!-- O campo de segredo: chave secreta, token ou PIN -->
        <div v-if="modal.tipo === 'app' || modal.tipo === 'chave'" class="relative">
          <label class="block text-xs">Chave secreta do app</label>
          <Input v-model="chaveSecreta" type="text" v-bind="CAMPO_SEGREDO" name="zap-chave-secreta" class="mt-1 pr-9 font-mono" :style="estiloSegredo" placeholder="cole aqui a chave secreta" />
          <button type="button" class="absolute bottom-2 right-2 text-muted-foreground hover:text-foreground" :title="verSegredo ? 'ocultar' : 'mostrar o que foi colado'" @click="verSegredo = !verSegredo"><EyeOff v-if="verSegredo" class="size-4" /><Eye v-else class="size-4" /></button>
        </div>
        <div v-if="modal.tipo === 'conta' || modal.tipo === 'token'" class="relative">
          <label class="block text-xs">Token do usuário do sistema</label>
          <Input v-model="tokenValor" type="text" v-bind="CAMPO_SEGREDO" name="zap-token" class="mt-1 pr-9 font-mono" :style="estiloSegredo" placeholder="cole aqui o token" />
          <button type="button" class="absolute bottom-2 right-2 text-muted-foreground hover:text-foreground" :title="verSegredo ? 'ocultar' : 'mostrar o que foi colado'" @click="verSegredo = !verSegredo"><EyeOff v-if="verSegredo" class="size-4" /><Eye v-else class="size-4" /></button>
        </div>
        <div v-if="modal.tipo === 'numero' || modal.tipo === 'registrar'" class="relative">
          <label class="block text-xs">PIN (6 números)</label>
          <Input v-model="pinValor" type="text" v-bind="CAMPO_SEGREDO" name="zap-pin" inputmode="numeric" maxlength="6" class="mt-1 pr-9 font-mono" :style="estiloSegredo" placeholder="••••••" />
          <button type="button" class="absolute bottom-2 right-2 text-muted-foreground hover:text-foreground" :title="verSegredo ? 'ocultar' : 'mostrar o que foi digitado'" @click="verSegredo = !verSegredo"><EyeOff v-if="verSegredo" class="size-4" /><Eye v-else class="size-4" /></button>
        </div>
        <p v-if="modal.tipo === 'app' || modal.tipo === 'chave' || modal.tipo === 'conta' || modal.tipo === 'token'" class="text-[11px] text-muted-foreground">Fica cifrado no servidor e não volta mais em tela, log ou link. Para trocar, cole um novo.</p>

        <div v-if="modalErro" class="text-sm text-red-500">{{ modalErro }}</div>

        <div class="flex justify-end gap-2">
          <Button variant="ghost" size="sm" :disabled="salvando" @click="fecharModal">cancelar</Button>
          <Button v-if="modal.tipo === 'app'" size="sm" :disabled="salvando" @click="salvarApp">{{ salvando ? 'salvando…' : 'Cadastrar app' }}</Button>
          <Button v-else-if="modal.tipo === 'conta'" size="sm" :disabled="salvando" @click="salvarConta">{{ salvando ? 'conferindo na Meta…' : 'Conferir e cadastrar' }}</Button>
          <Button v-else-if="modal.tipo === 'token'" size="sm" :disabled="salvando" @click="salvarToken">{{ salvando ? 'conferindo na Meta…' : 'Conferir e trocar' }}</Button>
          <Button v-else-if="modal.tipo === 'chave'" size="sm" :disabled="salvando" @click="salvarChave">{{ salvando ? 'trocando…' : 'Trocar chave secreta' }}</Button>
          <Button v-else-if="modal.tipo === 'numero'" size="sm" :disabled="salvando" @click="salvarNumero">{{ salvando ? 'salvando…' : 'Salvar' }}</Button>
          <Button v-else-if="modal.tipo === 'registrar'" size="sm" variant="destructive" :disabled="salvando" @click="registrar">{{ salvando ? 'registrando…' : 'Registrar (1 tentativa)' }}</Button>
        </div>
      </div>
    </div>
  </section>
</template>
