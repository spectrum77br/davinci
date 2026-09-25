<script setup lang="ts">
import { TABS_CADASTROS } from '~/lib/navGroups'
import { ref, computed, reactive, watch } from 'vue'
import { Plus, RefreshCw, X, ExternalLink, Trash2, Lock } from 'lucide-vue-next'
import {
  MARKETPLACES,
  MARKETPLACE_SHORT,
  STORE_STATUS_LABELS,
  STORE_STATUS_CLASSES,
  type Marketplace,
  type StoreStatus,
} from '~/composables/useMarketplaces'

definePageMeta({ middleware: ['permission'], permission: { resource: 'empresa', action: 'view' } })

type GridStoreCell = {
  id: string | null
  status: StoreStatus
  label: string
  integration_id: string | null
  bling_store_id: number | null
  from_store_info?: boolean
}

type CompanyOut = {
  id: string
  razao_social: string
  apelido: string
  responsavel_id: string | null
  responsavel_nome: string | null
  uf: string | null
  cnpj: string | null
  inscricao_estadual: string | null
  site_url: string | null
  operacao: string | null
  contabilidade: string | null
  ip: string | null
  // O que o serviço do Mac confirmou no AdsPower (só leitura).
  ip_adspower: string | null
  ip_adspower_em: string | null
  ip_adspower_erro: string | null
  obs: string | null
  enabled_marketplaces: string[]
  created_at: string
  updated_at: string
}

// Resumo do certificado digital que a tabela recebe — só vem para admin, e
// nunca traz o arquivo nem a senha (esses ficam nas rotas de certificado).
type CertificadoResumo = {
  id: string
  filename: string
  has_password: boolean
  expires_at: string | null
  total: number
}

type GridRow = {
  company: CompanyOut
  stores: Record<string, GridStoreCell | null>
  certificado?: CertificadoResumo | null
}
type GridOut = { marketplaces: string[]; rows: GridRow[] }

type StoreInfoLite = {
  id: string
  account_name: string | null
  cpf_name: string | null
  platform: string
  phone: string | null
  email: string | null
  server: string | null
}

const { api } = useApi()

// ---------- senha extra (a mesma do Valuation) ----------
// Eduardo, 25/09/2026: "senha segura porque tem informações que muita gente
// não pode ver". O servidor recusa os dados sem a chave; esta trava só mostra
// o cadeado e manda a chave em toda chamada.
const trava = useSenhaExtra('empresas', '/api/companies/unlock', 'X-Empresas-Token', /^\/companies(\/|$)/)

async function apiE<T = any>(path: string, opts: any = {}): Promise<T> {
  try {
    return await api<T>(path, { ...opts, headers: { ...(opts.headers || {}), ...trava.headers() } })
  } catch (e: any) {
    // Venceu no meio do uso: volta para o cadeado em vez de mostrar erro.
    if (trava.eTravamento(e)) bloquear()
    throw e
  }
}

function bloquear() {
  trava.trancar()
  grid.value = null
}
const grid = ref<GridOut | null>(null)
const storeInfos = ref<StoreInfoLite[]>([])
const loading = ref(false)
const error = ref<string | null>(null)
const filterMk = ref<string>('')
const filterUf = ref<string>('')
const filterResponsavel = ref<string>('')
const filterContabilidade = ref<string>('')
// Valor interno do filtro para empresa com a contabilidade em branco — usar
// string vazia colidiria com a opção "todas".
const SEM_CONTABILIDADE = '__sem__'
const search = ref<string>('')
const showNew = ref(false)

const canEdit = useCan('empresa', 'edit')
const canDelete = useCan('empresa', 'delete')
// Certificado digital é só de admin (mesma regra das rotas de certificado).
const isAdmin = useIsAdmin()

// Compara nomes de conta ignorando espaços e maiúsculas ("dream 2" == "dream2")
// — mesmo normalizador do backend (companies.py/_norm_conta).
const normConta = (s: string | null | undefined) => (s || '').replace(/\s+/g, '').toLowerCase()

async function refresh() {
  loading.value = true
  error.value = null
  try {
    const [gridRes, storeRes] = await Promise.all([
      apiE<GridOut>('/api/companies/grid'),
      apiE<StoreInfoLite[]>('/api/pricing/store-info').catch(() => [] as StoreInfoLite[]),
    ])
    grid.value = gridRes
    storeInfos.value = storeRes
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    loading.value = false
  }
}
// Sem `await refresh()` no carregamento: a chave só existe no navegador
// (sessionStorage), então a primeira carga acontece depois de montar a página.
// Carrega quando a chave aparece: já estava guardada na aba (iniciar) ou a
// pessoa acabou de digitar a senha. Não dá para esperar um aviso do cartão:
// ao desbloquear o cartão sai da tela, e o Vue descarta o aviso de componente
// que já saiu — a tabela ficava vazia (25/09/2026).
watch(() => trava.token.value, (agora, antes) => { if (agora && !antes) refresh() })
// A lista e a ficha dividem a chave: vindo de uma para a outra ela já existe
// e o observador acima não dispara, então carrega aqui.
onMounted(() => { if (trava.iniciar()) refresh() })

// Responsáveis conhecidos (para o filtro e para o autocompletar do campo),
// tirados da própria grade — o Responsável é um dado DA EMPRESA.
// Contabilidades existentes + QUANTAS empresas em cada uma. A contagem sai da
// grade inteira, não das linhas filtradas: o número tem que dizer quantas
// existem, senão ele mudaria conforme o próprio filtro e não serviria de guia.
const contabilidadeOpts = computed(() => {
  const conta = new Map<string, number>()
  for (const r of grid.value?.rows || []) {
    const v = (r.company.contabilidade || '').trim()
    const chave = v || SEM_CONTABILIDADE
    conta.set(chave, (conta.get(chave) || 0) + 1)
  }
  return Array.from(conta.entries())
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], 'pt-BR'))
    .map(([valor, total]) => ({ valor, total }))
})

const responsaveisOpts = computed(() => {
  const set = new Set<string>()
  for (const r of grid.value?.rows || []) {
    const n = (r.company.responsavel_nome || '').trim()
    if (n) set.add(n)
  }
  return Array.from(set).sort((a, b) => a.localeCompare(b, 'pt-BR'))
})

const filteredRows = computed(() => {
  if (!grid.value) return []
  let rows = grid.value.rows
  if (filterUf.value) rows = rows.filter(r => (r.company.uf || '').toUpperCase() === filterUf.value.toUpperCase())
  if (filterMk.value) rows = rows.filter(r => r.stores[filterMk.value] != null)
  if (filterResponsavel.value) {
    rows = rows.filter(r => (r.company.responsavel_nome || '').trim() === filterResponsavel.value)
  }
  if (filterContabilidade.value) {
    rows = rows.filter((r) => {
      const v = (r.company.contabilidade || '').trim()
      return filterContabilidade.value === SEM_CONTABILIDADE ? !v : v === filterContabilidade.value
    })
  }
  if (search.value) {
    const q = search.value.toLowerCase()
    rows = rows.filter(r => {
      const resp = (r.company.responsavel_nome || '').toLowerCase()
      // Contabilidade e operação entram na busca porque é o movimento natural:
      // digitar "CT" ou "INTERMEDIAÇÃO" aqui e esperar a lista filtrar. Sem
      // isso a busca devolvia zero e parecia defeito.
      const contab = (r.company.contabilidade || '').toLowerCase()
      const oper = (r.company.operacao || '').toLowerCase()
      const ip = (r.company.ip || '').toLowerCase()
      return (
        r.company.razao_social.toLowerCase().includes(q) ||
        r.company.apelido.toLowerCase().includes(q) ||
        (r.company.cnpj || '').includes(q) ||
        resp.includes(q) ||
        contab.includes(q) ||
        oper.includes(q) ||
        ip.includes(q)
      )
    })
  }
  return rows
})

const draft = ref({ razao_social: '', apelido: '', cnpj: '', uf: '', inscricao_estadual: '', site_url: '', operacao: '', contabilidade: '', ip: '', obs: '' })
const creating = ref(false)
const createErr = ref<string | null>(null)

async function createCompany() {
  creating.value = true
  createErr.value = null
  try {
    const body: Record<string, any> = {
      razao_social: draft.value.razao_social,
      apelido: draft.value.apelido,
    }
    for (const k of ['cnpj', 'uf', 'inscricao_estadual', 'site_url', 'operacao', 'contabilidade', 'obs'] as const) {
      if (draft.value[k]) body[k] = draft.value[k]
    }
    if (draft.value.ip.trim()) body.ip = soOIp(draft.value.ip)
    await apiE('/api/companies', { method: 'POST', body })
    showNew.value = false
    draft.value = { razao_social: '', apelido: '', cnpj: '', uf: '', inscricao_estadual: '', site_url: '', operacao: '', contabilidade: '', ip: '', obs: '' }
    await refresh()
  } catch (e: any) {
    // Two shapes: our HTTPException ({code: ...}) and Pydantic 422
    // (list of {loc, msg}). Surface the latter as "campo: motivo".
    const det = e?.data?.detail
    if (typeof det?.code === 'string' && det.code.startsWith('ip_')) {
      createErr.value = mensagemDeErro(e)
      return
    }
    if (Array.isArray(det)) {
      createErr.value = det.map((x: any) => {
        const field = Array.isArray(x?.loc) ? x.loc[x.loc.length - 1] : '?'
        const msg = (x?.msg || '').replace(/^Value error,\s*/i, '')
        return `${field}: ${msg}`
      }).join(' · ')
    } else {
      createErr.value = det?.code || e?.message || 'erro'
    }
  } finally {
    creating.value = false
  }
}

// ---------- inline Responsável edit ----------
// A chave é o ID DA EMPRESA, não o apelido: "dream 2" e "Dream 2" são duas
// empresas com o mesmo apelido normalizado — pelo apelido, clicar numa abria
// as duas e salvar numa escrevia na outra.
const editingResp = ref<string | null>(null) // company.id being edited
const respValue = ref('')
const respSaving = ref(false)
function startEditResp(row: GridRow) {
  if (!canEdit.value) return
  editingResp.value = row.company.id
  respValue.value = row.company.responsavel_nome || ''
}
function cancelEditResp() {
  editingResp.value = null
  respValue.value = ''
}
async function commitEditResp(row: GridRow) {
  if (editingResp.value !== row.company.id) return
  const next = respValue.value.trim()
  if (next === (row.company.responsavel_nome || '')) {
    cancelEditResp()
    return
  }
  respSaving.value = true
  try {
    // O Responsável é da EMPRESA: grava no cadastro dela e não depende de
    // existir loja. O responsável de cada LOJA continua na tela Lojas.
    await apiE(`/api/companies/${row.company.id}`, {
      method: 'PATCH',
      body: { responsavel_nome: next || null },
    })
    row.company.responsavel_nome = next || null
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    respSaving.value = false
    cancelEditResp()
  }
}

// ---------- generic inline edit for company text fields ----------
type EditableField = 'razao_social' | 'apelido' | 'uf' | 'cnpj' | 'inscricao_estadual' | 'site_url' | 'operacao' | 'contabilidade' | 'ip'

const editingCell = ref<{ id: string; field: EditableField } | null>(null)
const editCellValue = ref('')
const editCellSaving = ref(false)

function startEditCell(row: GridRow, field: EditableField) {
  if (!canEdit.value) return
  editingCell.value = { id: row.company.id, field }
  editCellValue.value = (row.company[field] || '') as string
}
function cancelEditCell() {
  editingCell.value = null
  editCellValue.value = ''
}
function isEditingCell(row: GridRow, field: EditableField) {
  return editingCell.value?.id === row.company.id && editingCell.value?.field === field
}
async function commitEditCell(row: GridRow, field: EditableField) {
  if (!isEditingCell(row, field)) return
  const next = field === 'ip' ? soOIp(editCellValue.value) : editCellValue.value.trim()
  const prev = (row.company[field] || '') as string
  if (next === prev.trim()) return cancelEditCell()
  // razao_social and apelido are non-null on the server; refuse to blank them.
  if ((field === 'razao_social' || field === 'apelido') && !next) {
    error.value = `${field === 'razao_social' ? 'Razão social' : 'Apelido'} não pode ficar vazio.`
    return cancelEditCell()
  }
  editCellSaving.value = true
  try {
    const updated = await apiE<CompanyOut>(`/api/companies/${row.company.id}`, {
      method: 'PATCH',
      body: { [field]: next || null },
    })
    row.company[field] = updated[field] as any
    if (field === 'ip') {
      row.company.ip_adspower = updated.ip_adspower
      row.company.ip_adspower_em = updated.ip_adspower_em
      row.company.ip_adspower_erro = updated.ip_adspower_erro
      if (updated.ip && updated.ip !== updated.ip_adspower) ligarRelogio()
    }
    // Deu certo: a faixa de erro de uma tentativa anterior (ex.: IP repetido)
    // não pode continuar na tela dizendo que falhou.
    error.value = null
  } catch (e: any) {
    error.value = mensagemDeErro(e)
  } finally {
    editCellSaving.value = false
    cancelEditCell()
  }
}

// Traduz os erros que a pessoa consegue resolver sozinha. O resto segue como
// o código cru, como já era.
function mensagemDeErro(e: any, padrao = 'erro'): string {
  const d = e?.data?.detail
  if (d?.code === 'ip_exists') {
    return d.empresa
      ? `Esse IP já é da empresa ${d.empresa}. Cada empresa precisa de um IP só dela.`
      : 'Esse IP já é de outra empresa. Cada empresa precisa de um IP só dela.'
  }
  if (d?.code === 'ip_invalido') {
    return 'IP inválido. Use o formato 72.60.155.3 — pode colar a linha do proxy, fica só o IP.'
  }
  if (d?.code === 'ip_nao_publico') {
    return 'Esse é um IP de rede interna. Coloque o IP público de saída do proxy.'
  }
  if (d?.code === 'senha_incorreta') return 'Senha do certificado incorreta.'
  if (d?.code === 'senha_obrigatoria') return 'Digite a senha do certificado.'
  if (d?.code === 'muitas_tentativas') {
    return 'Muitas tentativas com a senha errada. Espere 15 minutos para tentar de novo.'
  }
  return d?.code || e?.message || padrao
}

// Fica só com o IP do que foi colado, ANTES de mandar para o servidor: a linha
// do proxy do AdsPower vem como "ip:porta:usuario:senha", e a senha do proxy
// não tem por que sair do navegador. O servidor repete a mesma limpeza.
function soOIp(texto: string): string {
  let s = (texto || '').trim()
  s = s.replace(/^[a-z][a-z0-9+.-]*:\/\//i, '') // socks5://
  if (s.includes('@')) s = s.slice(s.lastIndexOf('@') + 1) // usuario:senha@
  if (s.startsWith('[')) {
    const fim = s.indexOf(']')
    return fim > 0 ? s.slice(1, fim) : s // [IPv6]:porta
  }
  const m = s.match(/^(\d{1,3}(?:\.\d{1,3}){3})(?::|$)/)
  return m ? m[1] : s // ip, ip:porta, ip:porta:usuario:senha
}

// ---------- IP (um por empresa) ----------
// O banco já recusa IP repetido; isto só pinta de vermelho se algum dado antigo
// escapou, para não passar despercebido.
const ipsRepetidos = computed(() => {
  const vezes = new Map<string, number>()
  for (const r of grid.value?.rows || []) {
    const ip = (r.company.ip || '').trim().toLowerCase()
    if (ip) vezes.set(ip, (vezes.get(ip) || 0) + 1)
  }
  return new Set([...vezes].filter(([, n]) => n > 1).map(([ip]) => ip))
})
function ipRepetido(row: GridRow) {
  const ip = (row.company.ip || '').trim().toLowerCase()
  return !!ip && ipsRepetidos.value.has(ip)
}

// ---------- IP no AdsPower ----------
// Quem aplica é o serviço do Mac, a cada minuto. A tela só mostra a situação.
type SituacaoAdspower = { simbolo: string; classe: string; texto: string }
function situacaoAdspower(c: CompanyOut): SituacaoAdspower | null {
  if (!c.ip) return null
  if (c.ip === c.ip_adspower) {
    const quando = c.ip_adspower_em ? new Date(c.ip_adspower_em).toLocaleString('pt-BR') : ''
    return { simbolo: '✓', classe: 'text-green-600', texto: `Já está no AdsPower${quando ? ` (desde ${quando})` : ''}` }
  }
  if (c.ip_adspower_erro) {
    return { simbolo: '✗', classe: 'text-red-600', texto: `Não foi para o AdsPower: ${c.ip_adspower_erro}. Tenta de novo sozinho em até 1 hora, ou na hora se você trocar o IP.` }
  }
  if (macSemResposta.value) {
    return { simbolo: '⏳', classe: 'text-amber-600', texto: 'O serviço do Mac ainda não aplicou (mais de 5 minutos). Ele pode estar desligado ou o AdsPower fechado.' }
  }
  return { simbolo: '⏳', classe: 'text-muted-foreground', texto: 'Indo para o AdsPower (leva até 1 minuto)' }
}
// Enquanto algum IP está a caminho, recarrega a tabela a cada 20 s para o
// relógio virar ✓ (ou ✗) sem a pessoa precisar apertar "recarregar". Para
// depois de 5 minutos: se o serviço do Mac está desligado, recarregar para
// sempre não resolve, e a tela passa a dizer isso.
const macSemResposta = ref(false)
let relogioAdspower: ReturnType<typeof setInterval> | null = null
let relogioDesde = 0
const algumIpACaminho = computed(() =>
  (grid.value?.rows || []).some(r => !!r.company.ip && r.company.ip !== r.company.ip_adspower && !r.company.ip_adspower_erro),
)
function pararRelogio() {
  if (relogioAdspower) clearInterval(relogioAdspower)
  relogioAdspower = null
}
// Recarrega só os dados, sem mexer na faixa de erro e sem atrapalhar quem está
// editando alguma coisa na tela.
async function recarregarEmSilencio() {
  if (!trava.token.value || loading.value || editingCell.value || editingResp.value || editingObs.value || certAberto.value || showNew.value) return
  try {
    grid.value = await apiE<GridOut>('/api/companies/grid')
  } catch {
    // silencioso: a tela continua com o que tem e tenta de novo em 20 s
  }
}
function ligarRelogio() {
  macSemResposta.value = false
  relogioDesde = Date.now()
  if (relogioAdspower) return
  relogioAdspower = setInterval(() => {
    if (Date.now() - relogioDesde > 5 * 60_000) {
      pararRelogio()
      macSemResposta.value = true
      return
    }
    recarregarEmSilencio()
  }, 20_000)
}
// `immediate`: se a página já abre com algum IP a caminho, liga na hora.
watch(algumIpACaminho, (sim) => {
  if (sim) ligarRelogio()
  else { pararRelogio(); macSemResposta.value = false }
}, { immediate: true })
onBeforeUnmount(pararRelogio)

// ---------- certificado digital ----------
// Clicar na célula abre um painel pequeno para pôr a senha (e o arquivo, se a
// empresa ainda não tem). A senha é a TRAVA do certificado (Eduardo,
// 25/09/2026): com senha, baixar pede a senha e trocar ou excluir a senha pede
// a atual. A senha guardada não aparece mais na tela — mostrar furava a trava.
const certAberto = ref<string | null>(null)
const certSenha = ref('')
// Senha atual do mais novo: trocar a senha de um certificado travado pede ela.
const certSenhaAtual = ref('')
const certMostrarDigitada = ref(false)
const certArquivo = ref<File | null>(null)
// Vencimento de um arquivo novo: sem ele o selo "vence DD/MM/AAAA" some justo no
// certificado renovado, que é o que vai vencer da próxima vez.
const certVence = ref('')
const certSalvando = ref(false)
const certErro = ref<string | null>(null)
// Todos os certificados da empresa aberta, para excluir um a um (Eduardo,
// 25/09/2026: "um botão para excluir o certificado"). Empresa com renovação
// subida em dobro tem mais de um; o resumo da tabela só conhece o mais novo.
type CertificadoItem = { id: string; filename: string; has_password: boolean; expires_at: string | null }
const certLista = ref<CertificadoItem[] | null>(null)
const certExcluindo = ref<string | null>(null)
// Baixar ou excluir a senha de UM certificado da lista abre um campo de senha
// embaixo dele. Certificado sem senha baixa direto.
type AcaoCertificado = { tipo: 'baixar' | 'tirar_senha'; id: string }
const certAcao = ref<AcaoCertificado | null>(null)
const certSenhaAcao = ref('')
const certAcaoRodando = ref(false)
const certBaixandoId = ref<string | null>(null)

function certOcupado() {
  return certSalvando.value || !!certExcluindo.value || certAcaoRodando.value
}
function fecharAcaoCertificado() {
  certAcao.value = null
  certSenhaAcao.value = ''
}
function limparPainelCertificado() {
  certSenha.value = ''
  certSenhaAtual.value = ''
  certMostrarDigitada.value = false
  certArquivo.value = null
  certVence.value = ''
  certErro.value = null
  certLista.value = null
  fecharAcaoCertificado()
}
function alternarCertificado(row: GridRow) {
  const abrindo = certAberto.value !== row.company.id
  limparPainelCertificado()
  certAberto.value = abrindo ? row.company.id : null
  if (abrindo && row.certificado) carregarListaCertificados(row.company.id)
}
async function carregarListaCertificados(empresa: string) {
  try {
    const lista = await apiE<CertificadoItem[]>(`/api/companies/${empresa}/certificates`)
    // A pessoa pode ter trocado de empresa enquanto a lista vinha.
    if (certAberto.value === empresa) certLista.value = lista
  } catch (e: any) {
    if (certAberto.value === empresa) certErro.value = mensagemDeErro(e, 'não foi possível listar os certificados')
  }
}
// Enquanto a lista não chega (ou se falhar), o mais novo da tabela já dá para excluir.
function certificadosDoPainel(row: GridRow): CertificadoItem[] {
  if (certLista.value) return certLista.value
  return row.certificado ? [row.certificado] : []
}
async function excluirCertificado(row: GridRow, cert: CertificadoItem) {
  if (certOcupado()) return
  const empresa = row.company.id
  const vence = cert.expires_at ? ` (vence ${dataBR(cert.expires_at)})` : ''
  if (!confirm(`Excluir o certificado ${cert.filename}${vence} de ${row.company.apelido}? O arquivo e a senha guardada são apagados de vez.`)) return
  certExcluindo.value = cert.id
  certErro.value = null
  try {
    try {
      await apiE(`/api/companies/${empresa}/certificates/${cert.id}`, { method: 'DELETE' })
    } catch (e: any) {
      // Já excluído em outra aba ou por outro admin: para a tela é o mesmo que ter dado certo.
      if (e?.data?.detail?.code !== 'certificate_not_found') throw e
    }
    tirarCertificadoDaTela(empresa, cert.id)
    await refresh()
    if (certAberto.value === empresa) await carregarListaCertificados(empresa)
  } catch (e: any) {
    if (certAberto.value === empresa) certErro.value = mensagemDeErro(e, 'erro ao excluir o certificado')
  } finally {
    certExcluindo.value = null
  }
}
// Some da tela logo depois do DELETE, sem esperar a recarga: se ela falhar (rede,
// trava da tela vencendo), o painel e o selo não ficam apontando para um
// certificado que já não existe.
function tirarCertificadoDaTela(empresa: string, certId: string) {
  if (certAberto.value === empresa) {
    if (certAcao.value?.id === certId) fecharAcaoCertificado()
    if (certLista.value) certLista.value = certLista.value.filter(c => c.id !== certId)
  }
  const linha = grid.value?.rows.find(r => r.company.id === empresa)
  const selo = linha?.certificado
  if (!linha || !selo) return
  if (selo.id !== certId) {
    selo.total = Math.max(1, selo.total - 1)
    return
  }
  // Era o mais novo: o próximo da lista (que vem do mais novo para o mais antigo)
  // passa a representar a empresa. Sem a lista, só dá para saber se era o único.
  const proximo = certAberto.value === empresa ? certLista.value?.[0] : undefined
  if (proximo) linha.certificado = { ...proximo, total: certLista.value!.length }
  else if (selo.total <= 1) linha.certificado = null
}
function fecharCertificado() {
  limparPainelCertificado()
  certAberto.value = null
}
function escolherArquivoCertificado(ev: Event) {
  const f = (ev.target as HTMLInputElement)?.files?.[0] || null
  certErro.value = null
  if (f && !/\.(p12|pfx)$/i.test(f.name)) {
    certErro.value = 'O arquivo do certificado precisa ser .pfx ou .p12.'
    certArquivo.value = null
    return
  }
  certArquivo.value = f
}
function pedirAcaoCertificado(row: GridRow, c: CertificadoItem, tipo: AcaoCertificado['tipo']) {
  if (certOcupado()) return
  certErro.value = null
  if (tipo === 'baixar' && !c.has_password) {
    fecharAcaoCertificado()
    baixarCertificado(row, c, '')
    return
  }
  certAcao.value = { tipo, id: c.id }
  certSenhaAcao.value = ''
}
async function confirmarAcaoCertificado(row: GridRow, c: CertificadoItem) {
  const acao = certAcao.value
  if (!acao || acao.id !== c.id || certOcupado()) return
  if (!certSenhaAcao.value.trim()) {
    certErro.value = acao.tipo === 'baixar'
      ? 'Digite a senha do certificado para baixar.'
      : 'Digite a senha atual para excluir a senha.'
    return
  }
  if (acao.tipo === 'baixar') await baixarCertificado(row, c, certSenhaAcao.value)
  else await tirarSenhaCertificado(row, c, certSenhaAcao.value)
}
// Com `responseType: 'blob'` o erro também chega como arquivo: lê o JSON de
// dentro para a mensagem ("senha incorreta") e a trava da tela funcionarem.
async function lerErroDeArquivo(e: any) {
  if (typeof Blob === 'undefined' || !(e?.data instanceof Blob)) return
  try {
    e.data = JSON.parse(await e.data.text())
  } catch {
    e.data = null
  }
  if (trava.eTravamento(e)) bloquear()
}
function salvarArquivoNoComputador(arquivo: Blob, nome: string) {
  const href = URL.createObjectURL(arquivo)
  const a = document.createElement('a')
  a.href = href
  a.download = nome
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(href)
}
async function baixarCertificado(row: GridRow, c: CertificadoItem, senha: string) {
  if (certAcaoRodando.value) return
  const empresa = row.company.id
  certAcaoRodando.value = true
  certBaixandoId.value = c.id
  certErro.value = null
  try {
    // POST com a senha no corpo (nunca na URL). Sem senha o servidor só entrega
    // se o certificado não tiver senha.
    const arquivo = await apiE<Blob>(`/api/companies/${empresa}/certificates/${c.id}/download`, {
      method: 'POST',
      body: { password: senha || null },
      responseType: 'blob',
    })
    salvarArquivoNoComputador(arquivo, c.filename)
    if (certAberto.value === empresa) fecharAcaoCertificado()
  } catch (e: any) {
    await lerErroDeArquivo(e)
    if (certAberto.value === empresa) certErro.value = mensagemDeErro(e, 'erro ao baixar o certificado')
  } finally {
    certAcaoRodando.value = false
    certBaixandoId.value = null
  }
}
async function tirarSenhaCertificado(row: GridRow, c: CertificadoItem, senhaAtual: string) {
  if (certAcaoRodando.value) return
  const empresa = row.company.id
  certAcaoRodando.value = true
  certErro.value = null
  try {
    await apiE(`/api/companies/${empresa}/certificates/${c.id}`, {
      method: 'PATCH',
      body: { password: null, current_password: senhaAtual },
    })
    if (certAberto.value === empresa) {
      fecharAcaoCertificado()
      if (certLista.value) {
        certLista.value = certLista.value.map(x => (x.id === c.id ? { ...x, has_password: false } : x))
      }
    }
    await refresh()
  } catch (e: any) {
    if (certAberto.value === empresa) certErro.value = mensagemDeErro(e, 'erro ao excluir a senha')
  } finally {
    certAcaoRodando.value = false
  }
}
async function salvarCertificado(row: GridRow) {
  // Enter apertado duas vezes (ou durante o envio) não pode subir o mesmo
  // certificado duas vezes; nem salvar no meio de uma exclusão ou download.
  if (certOcupado()) return
  const cert = row.certificado
  const empresa = row.company.id
  const senha = certSenha.value
  // A API tira os espaços das pontas: uma "senha" só de espaços chegaria vazia
  // e APAGARIA a senha guardada. Por isso a conta aqui é sobre o texto limpo.
  const temSenha = senha.trim() !== ''
  certErro.value = null
  if (!cert && !certArquivo.value) {
    certErro.value = 'Esta empresa ainda não tem certificado. Escolha o arquivo .pfx ou .p12.'
    return
  }
  if (cert && !certArquivo.value && !temSenha) {
    certErro.value = 'Digite a senha do certificado.'
    return
  }
  // Trocar a senha de um certificado travado pede a senha atual.
  if (cert && !certArquivo.value && cert.has_password && !certSenhaAtual.value.trim()) {
    certErro.value = 'Digite a senha atual para trocar a senha.'
    return
  }
  certSalvando.value = true
  try {
    if (certArquivo.value) {
      // Arquivo novo (primeiro certificado ou renovação): sobe junto com a senha.
      const fd = new FormData()
      fd.append('file', certArquivo.value)
      if (temSenha) fd.append('password', senha)
      if (certVence.value) fd.append('expires_at', certVence.value)
      await apiE(`/api/companies/${empresa}/certificates`, { method: 'POST', body: fd })
    } else if (cert) {
      // Só a senha, e nunca vazia: vazio apagaria a senha guardada.
      await apiE(`/api/companies/${empresa}/certificates/${cert.id}`, {
        method: 'PATCH',
        body: { password: senha, current_password: cert.has_password ? certSenhaAtual.value : null },
      })
    }
    if (certAberto.value === empresa) fecharCertificado()
    await refresh()
  } catch (e: any) {
    if (certAberto.value === empresa) certErro.value = mensagemDeErro(e, 'erro ao salvar o certificado')
  } finally {
    certSalvando.value = false
  }
}
// "2027-01-15" → "15/01/2027". Eduardo (25/09/2026): só mês/ano não bastava,
// precisa do dia em que vence.
function dataBR(iso: string): string {
  const [ano, mes, dia] = iso.split('-')
  return `${dia}/${mes}/${ano}`
}
// Situação para o selo da célula: vencido, vencendo em 30 dias, ou ok.
// Conta dias de calendário: no próprio dia do vencimento ainda "vence" (amarelo),
// "venceu" só a partir do dia seguinte.
function vencimentoCertificado(cert: { expires_at: string | null } | null | undefined) {
  if (!cert?.expires_at) return null
  const hoje = new Date()
  hoje.setHours(0, 0, 0, 0)
  const dias = Math.round((new Date(cert.expires_at + 'T00:00:00').getTime() - hoje.getTime()) / 86_400_000)
  return { texto: dataBR(cert.expires_at), vencido: dias < 0, vencendo: dias >= 0 && dias <= 30 }
}

// ---------- inline obs edit ----------
const editingObs = ref<string | null>(null)
const obsValue = ref('')
const obsSaving = ref(false)
function startEditObs(row: GridRow) {
  if (!canEdit.value) return
  editingObs.value = row.company.id
  obsValue.value = row.company.obs || ''
}
function cancelEditObs() {
  editingObs.value = null
  obsValue.value = ''
}
async function commitEditObs(row: GridRow) {
  if (editingObs.value !== row.company.id) return
  const next = obsValue.value.trim()
  const prev = row.company.obs || ''
  if (next === prev.trim()) return cancelEditObs()
  obsSaving.value = true
  try {
    await apiE(`/api/companies/${row.company.id}`, {
      method: 'PATCH',
      body: { obs: next || null },
    })
    row.company.obs = next || null
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    obsSaving.value = false
    cancelEditObs()
  }
}

// ---------- new account modal (Store + store_info) ----------
type CadastroLite = { id: string; codigo: string; label: string | null }

const newAccountFor = ref<{ company: CompanyOut; mk: Marketplace } | null>(null)
const newAccountForm = reactive({ phoneId: '', emailId: '', serverId: '' })
const availablePhones = ref<CadastroLite[]>([])
const availableEmails = ref<CadastroLite[]>([])
const availableServers = ref<CadastroLite[]>([])
const availableCadastrosLoading = ref(false)
const availableCadastrosLoaded = ref(false)
const availableCadastrosError = ref<string | null>(null)
let availableCadastrosRequest = 0
const newAccountSaving = ref(false)
const newAccountErr = ref<string | null>(null)
const newAccountResult = ref<string | null>(null)

async function loadAvailableCadastros(mk: Marketplace) {
  const target = newAccountFor.value
  if (!target || target.mk !== mk || newAccountSaving.value) return
  const request = ++availableCadastrosRequest
  const isCurrentRequest = () => (
    request === availableCadastrosRequest && newAccountFor.value === target && target.mk === mk
  )
  availableCadastrosLoading.value = true
  availableCadastrosLoaded.value = false
  availableCadastrosError.value = null
  availablePhones.value = []
  availableEmails.value = []
  availableServers.value = []
  newAccountForm.phoneId = ''
  newAccountForm.emailId = ''
  newAccountForm.serverId = ''
  try {
    const [phones, emails, servers] = await Promise.all([
      apiE<CadastroLite[]>(`/api/cadastros/available?tipo=fone&marketplace=${mk}`),
      apiE<CadastroLite[]>(`/api/cadastros/available?tipo=email&marketplace=${mk}`),
      apiE<CadastroLite[]>(`/api/cadastros/available?tipo=servidor&marketplace=${mk}`),
    ])
    if (!isCurrentRequest()) return
    availablePhones.value = phones
    availableEmails.value = emails
    availableServers.value = servers
    availableCadastrosLoaded.value = true
  } catch (e: any) {
    if (!isCurrentRequest()) return
    availableCadastrosError.value = e?.data?.detail?.code || e?.message || 'erro ao carregar cadastros'
  } finally {
    if (isCurrentRequest()) availableCadastrosLoading.value = false
  }
}

async function openNewAccount(row: GridRow, mk: Marketplace) {
  if (!canEdit.value) return
  newAccountFor.value = { company: row.company, mk }
  newAccountForm.phoneId = ''
  newAccountForm.emailId = ''
  newAccountForm.serverId = ''
  newAccountErr.value = null
  newAccountResult.value = null
  await loadAvailableCadastros(mk)
}

function closeNewAccount() {
  if (newAccountSaving.value) return
  availableCadastrosRequest++
  availableCadastrosLoading.value = false
  newAccountFor.value = null
}

async function submitNewAccount() {
  if (!newAccountFor.value || newAccountSaving.value || newAccountResult.value || availableCadastrosLoading.value || !availableCadastrosLoaded.value) return
  const target = newAccountFor.value
  const { company, mk } = target
  const phoneCad = availablePhones.value.find((c) => c.id === newAccountForm.phoneId)
  const emailCad = availableEmails.value.find((c) => c.id === newAccountForm.emailId)
  const serverCad = availableServers.value.find((c) => c.id === newAccountForm.serverId)
  if (!phoneCad || !emailCad || !serverCad) {
    newAccountErr.value = 'Fone, e-mail e servidor são obrigatórios.'
    return
  }
  newAccountSaving.value = true
  newAccountErr.value = null
  try {
    // A API reserva os cadastros e cria todos os vínculos na mesma transação.
    await apiE('/api/stores/account', {
      method: 'POST',
      body: {
        company_id: company.id,
        marketplace: mk,
        phone_id: phoneCad.id,
        email_id: emailCad.id,
        server_id: serverCad.id,
      },
    })

    newAccountResult.value =
      `Conta criada: ${company.apelido} · ${MARKETPLACE_SHORT[mk]} — ` +
      `Fone ${phoneCad.codigo} · Email ${emailCad.codigo} · Servidor ${serverCad.codigo}`
    await refresh()
    // Keep modal open briefly so user sees the result toast, then close.
    setTimeout(() => {
      if (newAccountFor.value === target) closeNewAccount()
    }, 1500)
  } catch (e: any) {
    const code = e?.data?.detail?.code
    if (code === 'cadastro_unavailable') {
      newAccountErr.value = 'Um dos cadastros já está em uso nesta plataforma. A lista foi atualizada; selecione novamente.'
      newAccountSaving.value = false
      await loadAvailableCadastros(mk)
    } else if (code === 'store_already_exists') {
      newAccountErr.value = 'Esta empresa já tem uma conta nesta plataforma. Recarregue a lista de empresas.'
    } else if (code === 'forbidden') {
      newAccountErr.value = 'Para criar a conta, você precisa de permissão para editar Empresas, Cadastros e Lojas.'
    } else {
      newAccountErr.value = code || e?.message || 'Não foi possível criar a conta.'
    }
  } finally {
    if (newAccountFor.value === target) newAccountSaving.value = false
  }
}

async function createStoreCell(companyId: string, mk: Marketplace) {
  // Kept for backward compat — the "+" button now opens the modal instead.
  const row = grid.value?.rows.find((r) => r.company.id === companyId)
  if (!row) return
  openNewAccount(row, mk)
}

// ---------- store cell popover (Ver detalhes / Remover conta) ----------
const cellPopoverFor = ref<string | null>(null) // `${companyId}:${mk}`
function openCellPopover(companyId: string, mk: Marketplace) {
  if (!canEdit.value) return
  const key = `${companyId}:${mk}`
  cellPopoverFor.value = cellPopoverFor.value === key ? null : key
}
function closeCellPopover() { cellPopoverFor.value = null }

const onDocClickCell = () => { cellPopoverFor.value = null }
onMounted(() => document.addEventListener('click', onDocClickCell))
onBeforeUnmount(() => document.removeEventListener('click', onDocClickCell))

function storeInfoFor(row: GridRow, mk: Marketplace): StoreInfoLite | undefined {
  const apelido = normConta(row.company.apelido)
  return storeInfos.value.find(
    (s) => s.platform === mk && normConta(s.account_name) === apelido,
  )
}

async function removeStoreCell(row: GridRow, mk: Marketplace) {
  const cell = row.stores[mk]
  if (!cell || !cell.id) return
  const apelido = row.company.apelido
  if (!confirm(`Remover conta de ${apelido} no ${MARKETPLACE_SHORT[mk]}? Vai apagar Loja, dados em store_info e vínculos em Cadastros.`)) return
  try {
    await apiE(`/api/stores/${cell.id}`, { method: 'DELETE' })
    closeCellPopover()
    await refresh()
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro ao remover'
  }
}

async function deleteCompany(row: GridRow) {
  if (!canDelete.value) return
  const { razao_social, apelido } = row.company
  // Cascade: apaga lojas, store_info e vínculos de Cadastros da empresa.
  if (!confirm(
    `Excluir a empresa "${apelido}" (${razao_social})?\n\n` +
    `Isso apaga TODAS as lojas, contas e vínculos dela. Não dá pra desfazer.`,
  )) return
  try {
    await apiE(`/api/companies/${row.company.id}`, { method: 'DELETE' })
    await refresh()
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro ao excluir'
  }
}

async function toggleMarketplaceEnabled(row: GridRow, mk: Marketplace) {
  if (!canEdit.value) return
  const enabled = new Set(row.company.enabled_marketplaces || [])
  const willEnable = !enabled.has(mk)
  const verb = willEnable ? 'Liberar' : 'Bloquear'
  if (!confirm(`${verb} ${MARKETPLACE_SHORT[mk]} para ${row.company.apelido}?`)) return
  if (willEnable) enabled.add(mk)
  else enabled.delete(mk)
  try {
    const updated = await apiE<CompanyOut>(`/api/companies/${row.company.id}`, {
      method: 'PATCH',
      body: { enabled_marketplaces: Array.from(enabled) },
    })
    row.company.enabled_marketplaces = updated.enabled_marketplaces
  } catch (e: any) {
    error.value = e?.data?.detail?.code || e?.message || 'erro'
  }
}
</script>

<template>
  <div class="space-y-4">
    <RouteTabs :tabs="TABS_CADASTROS" />
    <SenhaExtraTrava v-if="!trava.token.value" titulo="Empresas" :trava="trava" />
    <template v-else>
    <div class="flex items-center gap-3 flex-wrap">
      <h1 class="text-2xl font-semibold">Empresas</h1>
      <Button size="sm" variant="ghost" :disabled="loading" @click="refresh">
        <RefreshCw class="size-4 mr-1" /> recarregar
      </Button>
      <Button size="sm" variant="ghost" title="tranca a página de novo nesta aba" @click="bloquear">
        <Lock class="size-4 mr-1" /> bloquear
      </Button>
      <div class="ml-auto flex gap-2 flex-wrap">
        <Input v-model="search" placeholder="razão social / apelido / CNPJ / responsável / contabilidade" class="w-64" />
        <Input v-model="filterUf" placeholder="UF" class="w-20" />
        <select v-model="filterMk" class="border rounded px-2 text-sm bg-background">
          <option value="">todos marketplaces</option>
          <option v-for="mk in MARKETPLACES" :key="mk" :value="mk">{{ MARKETPLACE_SHORT[mk] }}</option>
        </select>
        <select v-model="filterResponsavel" class="border rounded px-2 text-sm bg-background">
          <option value="">todos responsáveis</option>
          <option v-for="r in responsaveisOpts" :key="r" :value="r">{{ r }}</option>
        </select>
        <select v-model="filterContabilidade" class="border rounded px-2 text-sm bg-background">
          <option value="">todas contabilidades</option>
          <option v-for="c in contabilidadeOpts" :key="c.valor" :value="c.valor">
            {{ c.valor === SEM_CONTABILIDADE ? 'sem contabilidade' : c.valor }} ({{ c.total }})
          </option>
        </select>
        <Button v-if="canEdit" size="sm" @click="showNew = true">
          <Plus class="size-4 mr-1" /> Nova empresa
        </Button>
      </div>
    </div>

    <div v-if="error" class="text-sm text-red-500">erro: {{ error }}</div>

    <div class="border rounded-md overflow-auto max-h-[calc(100vh-220px)]">
      <table class="w-full text-sm">
        <thead class="bg-muted text-left sticky top-0 z-10 shadow-[inset_0_-1px_0_var(--border)]">
          <tr>
            <th class="px-3 py-2 sticky left-0 bg-muted z-20">EMPRESA</th>
            <th class="px-3 py-2">UF</th>
            <th class="px-3 py-2">CNPJ</th>
            <th class="px-3 py-2">I.E.</th>
            <th class="px-3 py-2">conta</th>
            <th class="px-3 py-2">Responsável</th>
            <th class="px-3 py-2">operação</th>
            <th class="px-3 py-2">contabilidade</th>
            <th class="px-3 py-2" title="IP de saída da empresa nos marketplaces — cada empresa tem o seu, sem repetir">IP</th>
            <th v-if="isAdmin" class="px-3 py-2 whitespace-nowrap">certificado digital</th>
            <th v-for="mk in MARKETPLACES" :key="mk" class="px-2 py-2 text-center">
              {{ MARKETPLACE_SHORT[mk] }}
            </th>
            <th class="px-3 py-2">obs</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in filteredRows" :key="row.company.id" class="border-t hover:bg-muted/20">
            <td
              class="px-3 py-2 sticky left-0 z-10 bg-background"
              :class="{ 'cursor-pointer hover:bg-accent/30': canEdit && !isEditingCell(row, 'razao_social') }"
              @click="canEdit && !isEditingCell(row, 'razao_social') && startEditCell(row, 'razao_social')"
            >
              <input
                v-if="isEditingCell(row, 'razao_social')"
                v-model="editCellValue"
                type="text"
                class="w-full text-sm bg-transparent outline-none border-b border-blue-500 font-medium"
                :disabled="editCellSaving"
                autofocus
                @blur="commitEditCell(row, 'razao_social')"
                @keydown.enter.prevent="commitEditCell(row, 'razao_social')"
                @keydown.escape.prevent="cancelEditCell"
              />
              <div v-else class="flex items-center gap-1 group">
                <span class="font-medium flex-1 truncate" :title="row.company.razao_social">
                  {{ row.company.razao_social }}
                </span>
                <NuxtLink
                  :to="`/companies/${row.company.id}`"
                  class="opacity-0 group-hover:opacity-100 shrink-0 p-0.5 hover:bg-muted rounded"
                  title="Abrir empresa"
                  @click.stop
                >
                  <ExternalLink class="size-3 text-muted-foreground" />
                </NuxtLink>
                <button
                  v-if="canDelete"
                  class="opacity-0 group-hover:opacity-100 shrink-0 p-0.5 hover:bg-destructive/10 rounded"
                  title="Excluir empresa"
                  @click.stop="deleteCompany(row)"
                >
                  <Trash2 class="size-3 text-destructive" />
                </button>
              </div>
            </td>
            <td
              class="px-3 py-2"
              :class="{ 'cursor-pointer hover:bg-accent/30': canEdit && !isEditingCell(row, 'uf') }"
              @click="canEdit && !isEditingCell(row, 'uf') && startEditCell(row, 'uf')"
            >
              <input
                v-if="isEditingCell(row, 'uf')"
                v-model="editCellValue"
                type="text"
                maxlength="2"
                class="w-12 text-sm bg-transparent outline-none border-b border-blue-500 uppercase"
                :disabled="editCellSaving"
                autofocus
                @blur="commitEditCell(row, 'uf')"
                @keydown.enter.prevent="commitEditCell(row, 'uf')"
                @keydown.escape.prevent="cancelEditCell"
              />
              <span v-else :class="{ 'text-muted-foreground': !row.company.uf }">{{ row.company.uf || '—' }}</span>
            </td>
            <td
              class="px-3 py-2 font-mono text-xs"
              :class="{ 'cursor-pointer hover:bg-accent/30': canEdit && !isEditingCell(row, 'cnpj') }"
              @click="canEdit && !isEditingCell(row, 'cnpj') && startEditCell(row, 'cnpj')"
            >
              <input
                v-if="isEditingCell(row, 'cnpj')"
                v-model="editCellValue"
                type="text"
                class="w-full text-xs font-mono bg-transparent outline-none border-b border-blue-500"
                :disabled="editCellSaving"
                autofocus
                @blur="commitEditCell(row, 'cnpj')"
                @keydown.enter.prevent="commitEditCell(row, 'cnpj')"
                @keydown.escape.prevent="cancelEditCell"
              />
              <span v-else :class="{ 'text-muted-foreground': !row.company.cnpj }">{{ row.company.cnpj || '—' }}</span>
            </td>
            <td
              class="px-3 py-2"
              :class="{ 'cursor-pointer hover:bg-accent/30': canEdit && !isEditingCell(row, 'inscricao_estadual') }"
              @click="canEdit && !isEditingCell(row, 'inscricao_estadual') && startEditCell(row, 'inscricao_estadual')"
            >
              <input
                v-if="isEditingCell(row, 'inscricao_estadual')"
                v-model="editCellValue"
                type="text"
                class="w-full text-sm bg-transparent outline-none border-b border-blue-500"
                :disabled="editCellSaving"
                autofocus
                @blur="commitEditCell(row, 'inscricao_estadual')"
                @keydown.enter.prevent="commitEditCell(row, 'inscricao_estadual')"
                @keydown.escape.prevent="cancelEditCell"
              />
              <span v-else :class="{ 'text-muted-foreground': !row.company.inscricao_estadual }">
                {{ row.company.inscricao_estadual || '—' }}
              </span>
            </td>
            <td
              class="px-3 py-2"
              :class="{ 'cursor-pointer hover:bg-accent/30': canEdit && !isEditingCell(row, 'apelido') }"
              @click="canEdit && !isEditingCell(row, 'apelido') && startEditCell(row, 'apelido')"
            >
              <input
                v-if="isEditingCell(row, 'apelido')"
                v-model="editCellValue"
                type="text"
                class="w-full text-sm bg-transparent outline-none border-b border-blue-500"
                :disabled="editCellSaving"
                autofocus
                @blur="commitEditCell(row, 'apelido')"
                @keydown.enter.prevent="commitEditCell(row, 'apelido')"
                @keydown.escape.prevent="cancelEditCell"
              />
              <span v-else>{{ row.company.apelido }}</span>
            </td>
            <td
              class="px-3 py-2 text-xs max-w-40"
              :class="{
                'cursor-pointer hover:bg-accent/30': canEdit && editingResp !== row.company.id,
              }"
              :title="row.company.responsavel_nome || ''"
              @click="canEdit && editingResp !== row.company.id && startEditResp(row)"
            >
              <input
                v-if="editingResp === row.company.id"
                v-model="respValue"
                type="text"
                list="resp-nomes"
                class="w-full text-xs bg-transparent outline-none border-b border-blue-500"
                :disabled="respSaving"
                autofocus
                @blur="commitEditResp(row)"
                @keydown.enter.prevent="commitEditResp(row)"
                @keydown.escape.prevent="cancelEditResp"
              />
              <span
                v-else
                :class="{ 'text-muted-foreground': !row.company.responsavel_nome }"
                class="block truncate"
              >
                {{ row.company.responsavel_nome || '—' }}
              </span>
            </td>
            <td
              class="px-3 py-2 text-xs max-w-40"
              :class="{ 'cursor-pointer hover:bg-accent/30': canEdit && !isEditingCell(row, 'operacao') }"
              :title="row.company.operacao || ''"
              @click="canEdit && !isEditingCell(row, 'operacao') && startEditCell(row, 'operacao')"
            >
              <input
                v-if="isEditingCell(row, 'operacao')"
                v-model="editCellValue"
                type="text"
                class="w-full text-xs bg-transparent outline-none border-b border-blue-500"
                :disabled="editCellSaving"
                autofocus
                @blur="commitEditCell(row, 'operacao')"
                @keydown.enter.prevent="commitEditCell(row, 'operacao')"
                @keydown.escape.prevent="cancelEditCell"
              />
              <span v-else :class="{ 'text-muted-foreground': !row.company.operacao }" class="block truncate">
                {{ row.company.operacao || '—' }}
              </span>
            </td>
            <td
              class="px-3 py-2 text-xs max-w-40"
              :class="{ 'cursor-pointer hover:bg-accent/30': canEdit && !isEditingCell(row, 'contabilidade') }"
              :title="row.company.contabilidade || ''"
              @click="canEdit && !isEditingCell(row, 'contabilidade') && startEditCell(row, 'contabilidade')"
            >
              <input
                v-if="isEditingCell(row, 'contabilidade')"
                v-model="editCellValue"
                type="text"
                class="w-full text-xs bg-transparent outline-none border-b border-blue-500"
                :disabled="editCellSaving"
                autofocus
                @blur="commitEditCell(row, 'contabilidade')"
                @keydown.enter.prevent="commitEditCell(row, 'contabilidade')"
                @keydown.escape.prevent="cancelEditCell"
              />
              <span v-else :class="{ 'text-muted-foreground': !row.company.contabilidade }" class="block truncate">
                {{ row.company.contabilidade || '—' }}
              </span>
            </td>
            <td
              class="px-3 py-2 text-xs font-mono whitespace-nowrap"
              :class="{ 'cursor-pointer hover:bg-accent/30': canEdit && !isEditingCell(row, 'ip') }"
              :title="ipRepetido(row) ? 'Esse IP aparece em mais de uma empresa' : (row.company.ip || 'sem IP')"
              @click="canEdit && !isEditingCell(row, 'ip') && startEditCell(row, 'ip')"
            >
              <input
                v-if="isEditingCell(row, 'ip')"
                v-model="editCellValue"
                type="text"
                autocapitalize="off"
                spellcheck="false"
                placeholder="72.60.155.3"
                class="w-32 text-xs font-mono bg-transparent outline-none border-b border-blue-500"
                :disabled="editCellSaving"
                autofocus
                @blur="commitEditCell(row, 'ip')"
                @keydown.enter.prevent="commitEditCell(row, 'ip')"
                @keydown.escape.prevent="cancelEditCell"
              />
              <span
                v-else
                :class="ipRepetido(row) ? 'text-red-600 font-semibold' : !row.company.ip ? 'text-muted-foreground' : ''"
              >
                {{ row.company.ip || '—' }}<span v-if="ipRepetido(row)"> ⚠ repetido</span>
              </span>
              <span
                v-if="!isEditingCell(row, 'ip') && situacaoAdspower(row.company)"
                class="ml-1 font-sans"
                :class="situacaoAdspower(row.company)!.classe"
                :title="situacaoAdspower(row.company)!.texto"
                :aria-label="situacaoAdspower(row.company)!.texto"
              >{{ situacaoAdspower(row.company)!.simbolo }}</span>
            </td>
            <td v-if="isAdmin" class="px-3 py-2 text-xs whitespace-nowrap">
              <button
                type="button"
                class="rounded px-1.5 py-0.5 hover:bg-accent/40"
                :aria-expanded="certAberto === row.company.id"
                @click="alternarCertificado(row)"
              >
                <template v-if="!row.certificado">
                  <span class="text-muted-foreground">+ adicionar</span>
                </template>
                <template v-else>
                  <span v-if="row.certificado.has_password" class="text-green-600">🔒 com senha</span>
                  <span v-else class="text-amber-600">⚠ sem senha</span>
                  <span
                    v-if="vencimentoCertificado(row.certificado)"
                    class="ml-1"
                    :class="vencimentoCertificado(row.certificado)!.vencido
                      ? 'text-red-600 font-semibold'
                      : vencimentoCertificado(row.certificado)!.vencendo ? 'text-amber-600' : 'text-muted-foreground'"
                  >
                    · {{ vencimentoCertificado(row.certificado)!.vencido ? 'venceu' : 'vence' }}
                    {{ vencimentoCertificado(row.certificado)!.texto }}
                  </span>
                </template>
              </button>

              <!-- Abre fora da tabela: dentro dela a caixa de rolagem cortava o
                   painel, e com a busca filtrando uma empresa ele sumia. -->
              <Teleport to="body">
              <div
                v-if="certAberto === row.company.id"
                class="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4"
                @click.self="fecharCertificado"
                @keydown.escape="fecharCertificado"
              >
              <div
                role="dialog"
                aria-modal="true"
                :aria-label="`Certificado digital de ${row.company.apelido}`"
                class="w-full max-w-sm rounded-md border bg-background shadow-lg p-4 space-y-3 text-left text-xs whitespace-normal"
              >
                <div class="flex items-start justify-between gap-2">
                  <div class="font-medium">Certificado · {{ row.company.apelido }}</div>
                  <button type="button" class="text-muted-foreground hover:text-foreground" aria-label="fechar" @click="fecharCertificado">
                    <X class="h-3.5 w-3.5" />
                  </button>
                </div>

                <ul v-if="certificadosDoPainel(row).length" class="space-y-1 max-h-48 overflow-y-auto">
                  <li
                    v-for="(c, i) in certificadosDoPainel(row)"
                    :key="c.id"
                    class="rounded border px-2 py-1 space-y-1"
                  >
                    <div class="flex items-start justify-between gap-2">
                    <div class="min-w-0 text-muted-foreground">
                      <div class="truncate text-foreground" :title="c.filename">{{ c.filename }}</div>
                      <div>
                        <span v-if="c.has_password" class="text-green-600">🔒 com senha</span>
                        <span v-else class="text-amber-600">⚠ sem senha</span>
                        <span
                          v-if="vencimentoCertificado(c)"
                          :class="vencimentoCertificado(c)!.vencido
                            ? 'text-red-600 font-semibold'
                            : vencimentoCertificado(c)!.vencendo ? 'text-amber-600' : ''"
                        >
                          · {{ vencimentoCertificado(c)!.vencido ? 'venceu' : 'vence' }} {{ vencimentoCertificado(c)!.texto }}
                        </span>
                        <span v-else> · sem data de vencimento</span>
                        <span v-if="i === 0 && certificadosDoPainel(row).length > 1"> · o mais novo</span>
                      </div>
                    </div>
                    <div class="shrink-0 flex flex-col items-end gap-0.5">
                      <button
                        type="button"
                        class="text-blue-600 hover:underline disabled:opacity-50"
                        :disabled="certOcupado()"
                        @click="pedirAcaoCertificado(row, c, 'baixar')"
                      >
                        {{ certBaixandoId === c.id ? 'baixando…' : 'baixar' }}
                      </button>
                      <button
                        v-if="c.has_password"
                        type="button"
                        class="text-amber-700 hover:underline disabled:opacity-50"
                        :disabled="certOcupado()"
                        @click="pedirAcaoCertificado(row, c, 'tirar_senha')"
                      >
                        excluir senha
                      </button>
                      <button
                        type="button"
                        class="text-red-600 hover:underline disabled:opacity-50"
                        :disabled="certOcupado()"
                        @click="excluirCertificado(row, c)"
                      >
                        {{ certExcluindo === c.id ? 'excluindo…' : 'excluir' }}
                      </button>
                    </div>
                    </div>
                    <!-- Senha pedida na hora: para baixar (a senha é a trava) ou para
                         excluir a senha (pede a atual). -->
                    <div v-if="certAcao?.id === c.id" class="flex items-center gap-1">
                      <input
                        v-model="certSenhaAcao"
                        type="password"
                        autocomplete="off"
                        data-lpignore="true"
                        data-1p-ignore
                        :placeholder="certAcao.tipo === 'baixar' ? 'senha do certificado' : 'senha atual'"
                        :aria-label="certAcao.tipo === 'baixar' ? `Senha para baixar ${c.filename}` : `Senha atual de ${c.filename}`"
                        class="flex-1 min-w-0 border rounded px-2 py-1 bg-background"
                        @keydown.enter.prevent="confirmarAcaoCertificado(row, c)"
                        @keydown.escape.stop="fecharAcaoCertificado"
                      />
                      <button
                        type="button"
                        class="rounded px-2 py-1 text-white disabled:opacity-50"
                        :class="certAcao.tipo === 'baixar' ? 'bg-blue-600 hover:bg-blue-700' : 'bg-amber-600 hover:bg-amber-700'"
                        :disabled="certAcaoRodando"
                        @click="confirmarAcaoCertificado(row, c)"
                      >
                        {{ certAcao.tipo === 'baixar' ? 'baixar' : 'excluir senha' }}
                      </button>
                      <button type="button" class="border rounded px-2 py-1 hover:bg-accent/40" @click="fecharAcaoCertificado">
                        cancelar
                      </button>
                    </div>
                  </li>
                </ul>
                <div v-if="certificadosDoPainel(row).length > 1" class="text-muted-foreground">
                  A senha abaixo vale para o mais novo, que é o que aparece na tabela. Subir um arquivo novo
                  acrescenta outro certificado; os de cima continuam até você excluir.
                </div>

                <label v-if="row.certificado?.has_password && !certArquivo" class="block space-y-1">
                  <span>Senha atual (para trocar a senha)</span>
                  <input
                    v-model="certSenhaAtual"
                    type="password"
                    autocomplete="off"
                    data-lpignore="true"
                    data-1p-ignore
                    placeholder="senha atual"
                    class="w-full border rounded px-2 py-1 bg-background"
                    @keydown.enter.prevent="salvarCertificado(row)"
                  />
                </label>

                <label class="block space-y-1">
                  <span>{{ row.certificado?.has_password && !certArquivo ? 'Nova senha' : 'Senha do certificado' }}</span>
                  <div class="flex items-center gap-1">
                    <input
                      v-model="certSenha"
                      :type="certMostrarDigitada ? 'text' : 'password'"
                      autocomplete="off"
                      data-lpignore="true"
                      data-1p-ignore
                      :placeholder="row.certificado?.has_password ? 'digite para trocar' : 'digite a senha'"
                      class="flex-1 border rounded px-2 py-1 bg-background"
                      @keydown.enter.prevent="salvarCertificado(row)"
                    />
                    <button
                      type="button"
                      class="border rounded px-2 py-1 hover:bg-accent/40"
                      :title="certMostrarDigitada ? 'esconder' : 'mostrar o que estou digitando'"
                      @click="certMostrarDigitada = !certMostrarDigitada"
                    >
                      {{ certMostrarDigitada ? 'esconder' : 'mostrar' }}
                    </button>
                  </div>
                </label>

                <label class="block space-y-1">
                  <span>{{ row.certificado ? 'Subir arquivo novo (renovação) — opcional' : 'Arquivo do certificado (.pfx ou .p12)' }}</span>
                  <input type="file" accept=".pfx,.p12" class="block w-full text-xs" @change="escolherArquivoCertificado" />
                </label>

                <label v-if="certArquivo" class="block space-y-1">
                  <span>Vence em (opcional, mas mostra o aviso de vencimento)</span>
                  <input v-model="certVence" type="date" class="border rounded px-2 py-1 bg-background" />
                </label>

                <div v-if="certErro" class="text-red-600">{{ certErro }}</div>

                <div class="flex justify-end gap-2 pt-1">
                  <button type="button" class="border rounded px-3 py-1 hover:bg-accent/40" @click="fecharCertificado">cancelar</button>
                  <button
                    type="button"
                    class="rounded px-3 py-1 bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50"
                    :disabled="certOcupado()"
                    @click="salvarCertificado(row)"
                  >
                    {{ certSalvando ? 'salvando…' : 'salvar' }}
                  </button>
                </div>
              </div>
              </div>
              </Teleport>
            </td>
            <td v-for="mk in MARKETPLACES" :key="mk" class="px-2 py-2 text-center relative">
              <template v-if="row.stores[mk]">
                <button
                  v-if="canEdit"
                  :class="[STORE_STATUS_CLASSES[row.stores[mk]!.status], 'cursor-pointer hover:bg-accent/40 px-1.5 rounded']"
                  :title="`${MARKETPLACE_SHORT[mk]} — clique para opções`"
                  @click.stop="openCellPopover(row.company.id, mk)"
                >{{ STORE_STATUS_LABELS[row.stores[mk]!.status] }}</button>
                <span v-else :class="STORE_STATUS_CLASSES[row.stores[mk]!.status]">
                  {{ STORE_STATUS_LABELS[row.stores[mk]!.status] }}
                </span>
                <div
                  v-if="cellPopoverFor === `${row.company.id}:${mk}`"
                  class="absolute z-30 mt-1 left-1/2 -translate-x-1/2 w-56 rounded-md border bg-popover p-2 shadow-lg text-left text-xs"
                  @click.stop
                >
                  <div class="px-1 pb-2 border-b border-border space-y-0.5">
                    <div class="font-semibold uppercase text-[10px] text-muted-foreground">
                      {{ row.company.apelido }} · {{ MARKETPLACE_SHORT[mk] }}
                    </div>
                    <template v-if="storeInfoFor(row, mk)">
                      <div><span class="text-muted-foreground">Fone:</span> {{ storeInfoFor(row, mk)!.phone || '—' }}</div>
                      <div><span class="text-muted-foreground">Email:</span> {{ storeInfoFor(row, mk)!.email || '—' }}</div>
                      <div><span class="text-muted-foreground">Servidor:</span> {{ storeInfoFor(row, mk)!.server || '—' }}</div>
                    </template>
                    <div v-else class="text-muted-foreground">Sem store_info vinculada.</div>
                  </div>
                  <button
                    v-if="row.stores[mk]?.id"
                    class="w-full text-left mt-1 px-2 py-1 rounded hover:bg-destructive/10 text-destructive"
                    @click="removeStoreCell(row, mk)"
                  >
                    Remover conta
                  </button>
                  <p v-else class="mt-1 text-[10px] text-muted-foreground italic">
                    Detectado via store_info — sem registro em stores. Remova pela aba Lojas.
                  </p>
                </div>
              </template>
              <template v-else-if="!(row.company.enabled_marketplaces || []).includes(mk)">
                <button
                  v-if="canEdit"
                  class="text-red-500 font-semibold"
                  :title="`${MARKETPLACE_SHORT[mk]} bloqueado para esta empresa — clique para liberar`"
                  @click="toggleMarketplaceEnabled(row, mk)"
                >×</button>
                <span v-else class="text-red-500" :title="`${MARKETPLACE_SHORT[mk]} bloqueado`">×</span>
              </template>
              <button
                v-else-if="canEdit"
                class="text-muted-foreground hover:text-foreground"
                :title="`Criar loja em ${MARKETPLACE_SHORT[mk]}`"
                @click="createStoreCell(row.company.id, mk)"
              >+</button>
            </td>
            <td
              class="px-3 py-2 text-xs max-w-48"
              :class="{ 'cursor-pointer hover:bg-accent/30': canEdit && editingObs !== row.company.id }"
              :title="row.company.obs || ''"
              @click="canEdit && editingObs !== row.company.id && startEditObs(row)"
            >
              <input
                v-if="editingObs === row.company.id"
                v-model="obsValue"
                type="text"
                class="w-full text-xs bg-transparent outline-none border-b border-blue-500"
                :disabled="obsSaving"
                autofocus
                @blur="commitEditObs(row)"
                @keydown.enter.prevent="commitEditObs(row)"
                @keydown.escape.prevent="cancelEditObs"
              />
              <span v-else :class="{ 'text-muted-foreground': !row.company.obs }" class="block truncate">
                {{ row.company.obs || '—' }}
              </span>
            </td>
          </tr>
          <tr v-if="!loading && filteredRows.length === 0">
            <td :colspan="isAdmin ? 20 : 19" class="px-3 py-6 text-center text-muted-foreground">nenhuma empresa</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Nova conta (Store + store_info) -->
    <div
      v-if="newAccountFor"
      class="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4"
      @click.self="closeNewAccount"
    >
      <div class="bg-background border rounded-lg w-full max-w-md p-5 space-y-4">
        <div class="flex items-center">
          <div>
            <h2 class="text-lg font-semibold">Nova conta</h2>
            <p class="text-xs text-muted-foreground">
              {{ newAccountFor.company.apelido }} · {{ MARKETPLACE_SHORT[newAccountFor.mk] }}
            </p>
          </div>
          <Button class="ml-auto" size="sm" variant="ghost" :disabled="newAccountSaving" @click="closeNewAccount">
            <X class="size-4" />
          </Button>
        </div>
        <div class="space-y-3">
          <p v-if="availableCadastrosLoading" role="status" class="text-sm text-muted-foreground">
            Carregando cadastros disponíveis…
          </p>
          <div>
            <Label>Fone <span class="text-red-500">*</span></Label>
            <select
              v-model="newAccountForm.phoneId"
              :disabled="newAccountSaving || availableCadastrosLoading || !availableCadastrosLoaded"
              class="w-full border rounded px-2 py-1 bg-background text-sm"
            >
              <option value="">— selecione um fone disponível —</option>
              <option v-for="c in availablePhones" :key="c.id" :value="c.id">
                {{ c.codigo }}{{ c.label ? ` · ${c.label}` : '' }}
              </option>
            </select>
            <p v-if="availableCadastrosLoaded && !availablePhones.length" class="text-xs text-amber-600 mt-1">
              Sem fones disponíveis para esta plataforma. Cadastre um em /cadastros.
            </p>
          </div>
          <div>
            <Label>E-mail <span class="text-red-500">*</span></Label>
            <select
              v-model="newAccountForm.emailId"
              :disabled="newAccountSaving || availableCadastrosLoading || !availableCadastrosLoaded"
              class="w-full border rounded px-2 py-1 bg-background text-sm"
            >
              <option value="">— selecione um e-mail disponível —</option>
              <option v-for="c in availableEmails" :key="c.id" :value="c.id">
                {{ c.codigo }}{{ c.label ? ` · ${c.label}` : '' }}
              </option>
            </select>
            <p v-if="availableCadastrosLoaded && !availableEmails.length" class="text-xs text-amber-600 mt-1">
              Sem e-mails disponíveis para esta plataforma. Cadastre um em /cadastros.
            </p>
          </div>
          <div>
            <Label>Servidor <span class="text-red-500">*</span></Label>
            <select
              v-model="newAccountForm.serverId"
              :disabled="newAccountSaving || availableCadastrosLoading || !availableCadastrosLoaded"
              class="w-full border rounded px-2 py-1 bg-background text-sm"
            >
              <option value="">— selecione um servidor disponível —</option>
              <option v-for="c in availableServers" :key="c.id" :value="c.id">
                {{ c.codigo }}{{ c.label ? ` · ${c.label}` : '' }}
              </option>
            </select>
            <p v-if="availableCadastrosLoaded && !availableServers.length" class="text-xs text-amber-600 mt-1">
              Sem servidores disponíveis para esta plataforma. Cadastre um em /cadastros.
            </p>
          </div>
          <p class="text-xs text-muted-foreground">
            Apenas cadastros livres no {{ MARKETPLACE_SHORT[newAccountFor.mk] }} aparecem aqui.
            Cadastros usados somente em outras plataformas continuam disponíveis.
          </p>
        </div>
        <div v-if="availableCadastrosError" role="alert" class="space-y-2">
          <p class="text-sm text-red-500">Não foi possível carregar os cadastros: {{ availableCadastrosError }}</p>
          <Button variant="outline" size="sm" @click="loadAvailableCadastros(newAccountFor.mk)">
            Tentar novamente
          </Button>
        </div>
        <div v-if="newAccountErr" class="text-sm text-red-500">erro: {{ newAccountErr }}</div>
        <div v-if="newAccountResult" class="text-sm rounded border border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 px-3 py-2">
          ✓ {{ newAccountResult }}
        </div>
        <div class="flex justify-end gap-2">
          <Button variant="ghost" :disabled="newAccountSaving" @click="closeNewAccount">cancelar</Button>
          <Button
            :disabled="newAccountSaving || !!newAccountResult || availableCadastrosLoading || !availableCadastrosLoaded || !newAccountForm.phoneId || !newAccountForm.emailId || !newAccountForm.serverId"
            @click="submitNewAccount"
          >
            {{ newAccountSaving ? 'criando…' : 'Criar conta' }}
          </Button>
        </div>
      </div>
    </div>

    <div v-if="showNew" class="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4" @click.self="showNew = false">
      <div class="bg-background border rounded-lg w-full max-w-lg p-5 space-y-4">
        <div class="flex items-center">
          <h2 class="text-lg font-semibold">Nova empresa</h2>
          <Button class="ml-auto" size="sm" variant="ghost" @click="showNew = false">
            <X class="size-4" />
          </Button>
        </div>
        <div class="space-y-3">
          <div>
            <Label>Razão social *</Label>
            <Input v-model="draft.razao_social" required />
          </div>
          <div class="grid grid-cols-2 gap-3">
            <div>
              <Label>Apelido *</Label>
              <Input v-model="draft.apelido" required />
            </div>
            <div>
              <Label>UF</Label>
              <Input v-model="draft.uf" maxlength="2" />
            </div>
            <div>
              <Label>CNPJ</Label>
              <Input v-model="draft.cnpj" />
            </div>
            <div>
              <Label>I.E.</Label>
              <Input v-model="draft.inscricao_estadual" />
            </div>
          </div>
          <div>
            <Label>Site</Label>
            <Input v-model="draft.site_url" />
          </div>
          <div class="grid grid-cols-2 gap-3">
            <div>
              <Label>Operação</Label>
              <Input v-model="draft.operacao" />
            </div>
            <div>
              <Label>Contabilidade</Label>
              <Input v-model="draft.contabilidade" />
            </div>
            <div>
              <Label>IP</Label>
              <Input v-model="draft.ip" placeholder="72.60.155.3" autocapitalize="off" spellcheck="false" />
            </div>
          </div>
          <div>
            <Label>Observação</Label>
            <Input v-model="draft.obs" />
          </div>
        </div>
        <div v-if="createErr" class="text-sm text-red-500">erro: {{ createErr }}</div>
        <div class="flex justify-end gap-2">
          <Button variant="ghost" :disabled="creating" @click="showNew = false">cancelar</Button>
          <Button :disabled="creating || !draft.razao_social || !draft.apelido" @click="createCompany">
            {{ creating ? 'criando…' : 'Criar' }}
          </Button>
        </div>
      </div>
    </div>
    </template>
  </div>
  <datalist id="resp-nomes">
    <option v-for="n in responsaveisOpts" :key="n" :value="n" />
  </datalist>
</template>
