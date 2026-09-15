<script setup lang="ts">
// Cadastros › Marcas (Eduardo, 15/09/2026): as marcas da operação (poofy,
// locagil, 7buyers…) com situação no INPI, login/senha, e-mail, domínios e
// validade — vindas da aba `marcas` da planilha "redes sociais.xlsx". A aba
// Redes Sociais (/redes-sociais) pendura as contas por plataforma nesta lista.
//
// v3 (15/09/2026, tarde): a marca ganhou o que a ASSINATURA dos e-mails
// automáticos precisa — empresa (razão social/CNPJ, via company_id), site e
// logo (bytes no banco, PUT/DELETE /api/marcas/{id}/logo). Fone / e-mail /
// senha das redes e do SAC e a verificação do WhatsApp são da aba Redes
// Sociais (PATCH /api/redes-sociais/marca/{id}): aqui só aparecem como
// informação no modal, com link pra lá — MarcaCreate/MarcaPatch nem têm
// esses campos.
//
// Tela no molde de store-info.vue: tabela com edição inline por clique
// simples, PageHeader com recarregar/busca/toggle, modal de criar/editar
// (cadastros/index.vue). Senha NUNCA vem na listagem (`has_senha` só diz se
// existe): o olho busca em GET /api/marcas/{id}/senha, que exige edit e fica
// no log do backend. Regras de higiene da senha (SPEC v2): revela só no
// clique, esconde sozinha em 30 s, some ao recarregar/filtrar/sair da tela,
// e "copiar" busca e escreve no clipboard sem guardar.
import { TABS_CADASTROS } from '~/lib/navGroups'
import { apiErrMsg, MARCAS_ERROS } from '~/lib/apiError'
import { isoToday } from '~/lib/date'
import { INPI_STATUS, INPI_STATUS_LABELS, INPI_STATUS_PILL, fmtFone, type InpiStatus } from '~/lib/redesSociais'
import { AlertCircle, Archive, Check, Copy, ExternalLink, Eye, EyeOff, Loader2, Pencil, Plus, RefreshCw, Trash2, Upload, X } from 'lucide-vue-next'

definePageMeta({
  middleware: ['permission'],
  permission: { resource: 'marcas', action: 'view' },
})

// Espelha app/schemas/marcas.py::MarcaOut — sem senha, só `has_senha`
// (idem `has_sac_senha`); `has_logo` diz se tem logo sem trazer os bytes.
type MarcaOut = {
  id: string
  nome: string
  slug: string
  inpi_status: InpiStatus | string
  usuario: string | null
  has_senha: boolean
  email: string | null
  dominio_br: string | null
  dominio: string | null
  dono_dominio: string | null
  dominio_validade: string | null
  classe: string | null
  funcao: string | null
  tipo: string | null
  obs: string | null
  ativo: boolean
  // Só leitura aqui (dono: aba Redes Sociais).
  sac_fone: string | null
  sac_email: string | null
  has_sac_senha: boolean
  whatsapp_verificacao_status: string
  whatsapp_verificacao_obs: string | null
  // Assinatura dos e-mails.
  company_id: string | null
  empresa_razao_social: string | null
  site: string | null
  has_logo: boolean
  created_at: string
  updated_at: string
}

// GET /api/marcas/empresas (schemas/marcas.py::EmpresaRef) — select do modal.
type EmpresaRef = { id: string; apelido: string; razao_social: string; cnpj: string | null }

// Campos de texto editáveis inline (clique simples). Senha e validade NÃO:
// senha só pelo modal (mascarada), validade é <input type=date> no modal.
// Empresa também só pelo modal (é um select de company_id).
type TextField =
  | 'nome' | 'site' | 'classe' | 'funcao' | 'tipo' | 'usuario'
  | 'email' | 'dominio_br' | 'dominio' | 'dono_dominio' | 'obs'
type EditField = TextField | 'inpi_status'

// Colunas na ordem da tela — thead, tbody e colspan saem daqui, então não
// desalinham (padrão dos loops de cadastros/index.vue). `truncate` = célula
// compacta com o texto inteiro no title.
type Col =
  | { kind: 'text'; key: TextField; label: string; th?: string; truncate?: boolean }
  | { kind: 'inpi' | 'empresa' | 'senha' | 'validade' | 'ativo' | 'acoes'; key: string; label: string; th?: string }

const COLS: Col[] = [
  { kind: 'text', key: 'nome', label: 'Marca', th: 'min-w-[140px]' },
  { kind: 'empresa', key: 'empresa', label: 'Empresa', th: 'min-w-[140px]' },
  { kind: 'text', key: 'site', label: 'Site', th: 'min-w-[150px]', truncate: true },
  { kind: 'inpi', key: 'inpi_status', label: 'INPI', th: 'min-w-[120px]' },
  { kind: 'text', key: 'classe', label: 'Classe' },
  { kind: 'text', key: 'funcao', label: 'Função' },
  { kind: 'text', key: 'tipo', label: 'Tipo' },
  { kind: 'text', key: 'usuario', label: 'Usuário', th: 'min-w-[120px]' },
  { kind: 'senha', key: 'senha', label: 'Senha', th: 'min-w-[120px]' },
  { kind: 'text', key: 'email', label: 'E-mail', th: 'min-w-[180px]', truncate: true },
  { kind: 'text', key: 'dominio_br', label: 'Domínio .br', th: 'min-w-[160px]', truncate: true },
  { kind: 'text', key: 'dominio', label: 'Domínio', th: 'min-w-[140px]', truncate: true },
  { kind: 'text', key: 'dono_dominio', label: 'Dono' },
  { kind: 'validade', key: 'dominio_validade', label: 'Validade domínio', th: 'whitespace-nowrap' },
  { kind: 'text', key: 'obs', label: 'Obs', th: 'min-w-[160px]', truncate: true },
  { kind: 'ativo', key: 'ativo', label: 'Ativo', th: 'text-center w-16' },
  { kind: 'acoes', key: 'acoes', label: '', th: 'w-20 text-right whitespace-nowrap' },
]

const { api } = useApi()
const canEdit = useCan('marcas', 'edit')
const canDelete = useCan('marcas', 'delete')

// Senhas reveladas na tabela (SPEC v2): id → plaintext só enquanto o olho
// está aberto; timer de auto-ocultar por id. Declarado ANTES do load() porque
// ele chama clearRevealed() já na carga inicial (top-level await).
const REVEAL_TTL_MS = 30_000
const revealed = ref<Set<string>>(new Set())
const revealedPasswords = ref<Map<string, string>>(new Map())
const revealTimers = new Map<string, ReturnType<typeof setTimeout>>()

function hideSenha(id: string) {
  revealed.value.delete(id)
  revealedPasswords.value.delete(id)
  const t = revealTimers.get(id)
  if (t) {
    clearTimeout(t)
    revealTimers.delete(id)
  }
}

function clearRevealed() {
  for (const t of revealTimers.values()) clearTimeout(t)
  revealTimers.clear()
  revealed.value.clear()
  revealedPasswords.value.clear()
}

const items = ref<MarcaOut[]>([])
const loading = ref(false)
const error = ref<string | null>(null)
const search = ref('')
// false = marcas ativas (default); true = aba "Inativas" (mesma ideia do
// toggle Arquivadas de store-info). A API filtra por ?ativo=.
const showInativas = ref(false)

// Empresas do select "Empresa da assinatura": buscadas UMA vez, na primeira
// abertura do modal (quem só vê a lista não paga a requisição); recarregar
// a página invalida o cache.
const empresas = ref<EmpresaRef[]>([])
const empresasLoaded = ref(false)
const empresasLoading = ref(false)

async function load() {
  loading.value = true
  error.value = null
  // Recarregar descarta qualquer senha revelada (SPEC v2).
  clearRevealed()
  empresasLoaded.value = false
  try {
    items.value = await api<MarcaOut[]>(`/api/marcas?ativo=${showInativas.value ? 'false' : 'true'}`)
  } catch (e: any) {
    error.value = apiErrMsg(e, MARCAS_ERROS)
  } finally {
    loading.value = false
  }
}

await load()

function toggleInativas() {
  showInativas.value = !showInativas.value
  load()
}

// Busca no cliente pelos mesmos campos do `search` da API (routers/marcas.py)
// + empresa e site, que são colunas visíveis aqui.
const filtered = computed(() => {
  const q = search.value.trim().toLowerCase()
  if (!q) return items.value
  return items.value.filter((m) =>
    [m.nome, m.slug, m.dominio_br, m.dominio, m.classe, m.empresa_razao_social, m.site]
      .some((v) => (v || '').toLowerCase().includes(q)),
  )
})
// Filtrar também esconde o que estava revelado.
watch(search, () => clearRevealed())

function cell(row: MarcaOut, key: TextField): string | null {
  return row[key]
}

// Atualização de uma linha vinda da API (PATCH, PUT/DELETE do logo): muda no
// lugar pra célula/miniatura/modal (que apontam pro mesmo objeto) refletirem.
function applyRow(updated: MarcaOut): MarcaOut {
  const row = items.value.find((x) => x.id === updated.id)
  if (row) {
    Object.assign(row, updated)
    return row
  }
  return updated
}

// =========================================================== logo / site / empresa

// Miniatura: o `?v=updated_at` fura o cache (Cache-Control private,
// max-age=300 do GET /logo) quando o logo é trocado — o PUT bumpa updated_at.
function logoSrc(m: Pick<MarcaOut, 'id' | 'updated_at'>): string {
  return `/api/marcas/${m.id}/logo?v=${encodeURIComponent(m.updated_at)}`
}

// Mesma regra do backend (schemas/marcas._site): precisa começar com http(s)://.
function siteValido(s: string): boolean {
  return /^https?:\/\/\S+$/i.test(s.trim())
}

// Texto da opção do select de empresas (spec: `${apelido} — ${razao_social}`).
function empresaLabel(c: EmpresaRef): string {
  return `${c.apelido} — ${c.razao_social}`
}

async function ensureEmpresas() {
  if (empresasLoaded.value || empresasLoading.value) return
  empresasLoading.value = true
  try {
    empresas.value = await api<EmpresaRef[]>('/api/marcas/empresas')
    empresasLoaded.value = true
  } catch (e: any) {
    saveErr.value = apiErrMsg(e, MARCAS_ERROS)
  } finally {
    empresasLoading.value = false
  }
}

// =========================================================== INPI / datas

function inpiPill(s: string): string {
  return INPI_STATUS_PILL[s as InpiStatus] || 'pill-muted'
}
function inpiLabel(s: string): string {
  return INPI_STATUS_LABELS[s as InpiStatus] || s
}

// Data ISO → pt-BR sem `new Date(iso)` (que cai em UTC e volta um dia) —
// mesmo helper de faturas.vue.
function fmtDate(s: string | null) {
  if (!s) return '—'
  const [y, m, d] = s.split('-').map((n) => Number(n))
  if (!y || !m || !d) return s
  return new Date(y, m - 1, d).toLocaleDateString('pt-BR')
}

// Dias até a validade (negativo = já venceu). Compara datas locais (faturas.vue).
function daysUntil(s: string): number {
  const [y, m, d] = s.split('-').map((n) => Number(n))
  const venc = new Date(y, m - 1, d)
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  venc.setHours(0, 0, 0, 0)
  return Math.round((venc.getTime() - today.getTime()) / 86_400_000)
}

function validadeVencida(s: string | null): boolean {
  // Comparação de strings ISO com o hoje em BRT (lib/date) — sem Date/UTC.
  return !!s && s < isoToday()
}

function validadeClass(s: string | null): string {
  if (!s) return 'text-muted-foreground'
  if (validadeVencida(s)) return 'text-red-500 font-semibold'
  if (daysUntil(s) <= 90) return 'text-amber-500'
  return ''
}

// =========================================================== inline edit
// Padrão store-info.vue: clique abre input na célula (ring azul), Enter/blur
// salva PATCH parcial, Esc cancela, flash verde 1,2 s ao salvar.

const editing = ref<{ id: string; field: EditField } | null>(null)
const editValue = ref<string>('')
const editOriginal = ref<string>('')
const editInputRef = ref<HTMLInputElement | HTMLSelectElement | null>(null)
function setEditInputRef(el: any) {
  if (el) editInputRef.value = el
}
const flashed = ref<Set<string>>(new Set())

function isEditing(id: string, field: EditField) {
  return editing.value?.id === id && editing.value?.field === field
}

function isFlashed(id: string, field: EditField) {
  return flashed.value.has(`${id}::${field}`)
}

function flash(id: string, field: EditField) {
  const k = `${id}::${field}`
  flashed.value.add(k)
  setTimeout(() => flashed.value.delete(k), 1200)
}

async function startEdit(row: MarcaOut, field: EditField) {
  if (!canEdit.value) return
  editing.value = { id: row.id, field }
  const raw = row[field]
  const initial = raw == null ? '' : String(raw)
  editValue.value = initial
  editOriginal.value = initial
  await nextTick()
  const el = editInputRef.value
  if (el) {
    el.focus()
    if ('select' in el) (el as HTMLInputElement).select?.()
  }
}

function cancelEdit() {
  editing.value = null
  editValue.value = ''
  editOriginal.value = ''
}

async function commitEdit() {
  if (!editing.value) return
  const { id, field } = editing.value
  const raw = String(editValue.value ?? '').trim()
  const changed = editValue.value !== editOriginal.value
  // Fecha o input ANTES do PATCH: o <select> do INPI dispara change E blur
  // (e o input, Enter E blur) — se o estado ficasse aberto até a resposta,
  // o segundo evento mandaria um PATCH repetido.
  cancelEdit()
  if (!changed) return
  const row = items.value.find((x) => x.id === id)
  if (!row) return

  const payload: Record<string, unknown> = {}
  if (field === 'nome') {
    // nome é obrigatório (schemas/marcas.py nome_required) — vazio não salva.
    if (!raw) return
    payload.nome = raw
  } else if (field === 'inpi_status') {
    payload.inpi_status = raw
  } else if (field === 'site') {
    // Mesma validação do backend (site_invalido), sem ida ao servidor.
    if (raw && !siteValido(raw)) {
      error.value = MARCAS_ERROS.site_invalido
      return
    }
    payload.site = raw || null
  } else {
    payload[field] = raw || null
  }

  try {
    const updated = await api<MarcaOut>(`/api/marcas/${id}`, { method: 'PATCH', body: payload })
    applyRow(updated)
    flash(id, field)
  } catch (e: any) {
    error.value = apiErrMsg(e, MARCAS_ERROS)
  }
}

// =========================================================== ativo

const ativoBusy = ref<Set<string>>(new Set())

async function toggleAtivo(row: MarcaOut) {
  if (!canEdit.value || ativoBusy.value.has(row.id)) return
  ativoBusy.value.add(row.id)
  try {
    const updated = await api<MarcaOut>(`/api/marcas/${row.id}`, { method: 'PATCH', body: { ativo: !row.ativo } })
    // A lista é filtrada por ?ativo= — a marca muda de aba (Ativas ⇄
    // Inativas) e sai daqui, como o arquivar de store-info.
    items.value = items.value.filter((x) => x.id !== updated.id)
    hideSenha(row.id)
  } catch (e: any) {
    error.value = apiErrMsg(e, MARCAS_ERROS)
  } finally {
    ativoBusy.value.delete(row.id)
  }
}

// =========================================================== delete

async function remove(row: MarcaOut) {
  if (!confirm(`Excluir a marca "${row.nome}"?\n\nAs contas de redes sociais e os padrões de e-mail dessa marca são excluídos junto. Não dá pra desfazer.`)) return
  try {
    await api(`/api/marcas/${row.id}`, { method: 'DELETE' })
    items.value = items.value.filter((x) => x.id !== row.id)
    hideSenha(row.id)
  } catch (e: any) {
    error.value = apiErrMsg(e, MARCAS_ERROS)
  }
}

// =========================================================== senha (tabela)
// store-info.vue:725-762, com os cuidados da SPEC v2: revela só no clique,
// esconde sozinha em 30 s, e tudo some em recarregar/filtro/onUnmounted
// (estado e clearRevealed ficam declarados antes do load(), lá em cima).

async function toggleReveal(id: string) {
  if (revealed.value.has(id)) {
    hideSenha(id)
    return
  }
  try {
    const r = await api<{ senha: string }>(`/api/marcas/${id}/senha`)
    revealedPasswords.value.set(id, r.senha)
    revealed.value.add(id)
    revealTimers.set(id, setTimeout(() => hideSenha(id), REVEAL_TTL_MS))
  } catch (e: any) {
    error.value = apiErrMsg(e, MARCAS_ERROS)
  }
}

const copiedId = ref<string | null>(null)
async function copySenha(id: string) {
  // Se não está revelada, busca e escreve direto no clipboard — sem guardar.
  let pw = revealedPasswords.value.get(id)
  if (pw == null) {
    try {
      pw = (await api<{ senha: string }>(`/api/marcas/${id}/senha`)).senha
    } catch (e: any) {
      error.value = apiErrMsg(e, MARCAS_ERROS)
      return
    }
  }
  try {
    await navigator.clipboard.writeText(pw ?? '')
    copiedId.value = id
    setTimeout(() => { if (copiedId.value === id) copiedId.value = null }, 1200)
  } catch { /* clipboard indisponível */ }
}

onUnmounted(() => clearRevealed())

// =========================================================== modal
// Mesmo form pra "Nova marca" e "Editar marca" (cadastros/index.vue). Senha
// no padrão nf-cadastros.vue:575-600: <Input type=text> com
// -webkit-text-security (não aciona o gerenciador de senhas do navegador),
// olho revela a senha salva via GET /senha, "branco = manter" no editar,
// "limpar senha" manda senha: null. O campo é zerado ao fechar.

type MarcaForm = {
  nome: string
  inpi_status: InpiStatus
  // '' = nenhuma empresa (vira company_id: null no corpo).
  company_id: string
  site: string
  usuario: string
  senha: string
  email: string
  dominio_br: string
  dominio: string
  dono_dominio: string
  dominio_validade: string
  classe: string
  funcao: string
  tipo: string
  obs: string
  ativo: boolean
}

function emptyForm(): MarcaForm {
  return {
    nome: '', inpi_status: 'nao_registrado', company_id: '', site: '',
    usuario: '', senha: '', email: '',
    dominio_br: '', dominio: '', dono_dominio: '', dominio_validade: '',
    classe: '', funcao: '', tipo: '', obs: '', ativo: true,
  }
}

const showModal = ref(false)
const modalEditing = ref<MarcaOut | null>(null)
const form = ref<MarcaForm>(emptyForm())
const saving = ref(false)
const saveErr = ref<string | null>(null)
const senhaVisible = ref(false)
const senhaRevealing = ref(false)
// Senha salva que o olho trouxe do backend: se o campo continua igual a ela
// no salvar, não reenviamos (evita re-cifrar e trafegar a senha à toa).
const senhaRevelada = ref<string | null>(null)
// "limpar senha": o PATCH manda senha: null. Digitar uma senha nova vence.
const limparSenha = ref(false)

function resetSenhaState() {
  form.value.senha = ''
  senhaVisible.value = false
  senhaRevealing.value = false
  senhaRevelada.value = null
  limparSenha.value = false
}

function openNew() {
  form.value = emptyForm()
  modalEditing.value = null
  saveErr.value = null
  logoErr.value = null
  resetSenhaState()
  showModal.value = true
  void ensureEmpresas()
}

function openEdit(m: MarcaOut) {
  form.value = {
    nome: m.nome,
    inpi_status: (INPI_STATUS.includes(m.inpi_status as InpiStatus) ? m.inpi_status : 'nao_registrado') as InpiStatus,
    company_id: m.company_id || '',
    site: m.site || '',
    usuario: m.usuario || '',
    senha: '',
    email: m.email || '',
    dominio_br: m.dominio_br || '',
    dominio: m.dominio || '',
    dono_dominio: m.dono_dominio || '',
    dominio_validade: m.dominio_validade || '',
    classe: m.classe || '',
    funcao: m.funcao || '',
    tipo: m.tipo || '',
    obs: m.obs || '',
    ativo: m.ativo,
  }
  modalEditing.value = m
  saveErr.value = null
  logoErr.value = null
  resetSenhaState()
  showModal.value = true
  void ensureEmpresas()
}

function closeModal() {
  showModal.value = false
  modalEditing.value = null
  // Zera a senha ao fechar — nada de plaintext sobrando no estado da tela.
  resetSenhaState()
}

async function toggleSenha() {
  if (senhaVisible.value) { senhaVisible.value = false; return }
  const m = modalEditing.value
  if (m && m.has_senha && !form.value.senha && !limparSenha.value) {
    senhaRevealing.value = true
    try {
      const r = await api<{ senha: string }>(`/api/marcas/${m.id}/senha`)
      form.value.senha = r.senha || ''
      senhaRevelada.value = form.value.senha
    } catch (e: any) { saveErr.value = apiErrMsg(e, MARCAS_ERROS); return }
    finally { senhaRevealing.value = false }
  }
  senhaVisible.value = true
}

function marcarLimparSenha() {
  limparSenha.value = true
  form.value.senha = ''
  senhaVisible.value = false
  senhaRevelada.value = null
}

// Corpo do POST/PATCH: texto vazio → null; validade "" → null; empresa ""
// → company_id: null (limpar o select desvincula); `senha` só entra quando
// digitada (e diferente da salva revelada) — ou null quando o usuário pediu
// pra limpar. `ativo` só no editar (criar nasce ativa).
function buildBody(f: MarcaForm, opts: { editing: boolean; limpar: boolean; senhaSalva: string | null }): Record<string, unknown> {
  const t = (s: string) => s.trim() || null
  const body: Record<string, unknown> = {
    nome: f.nome.trim(),
    inpi_status: f.inpi_status,
    company_id: f.company_id || null,
    site: t(f.site),
    usuario: t(f.usuario),
    email: t(f.email),
    dominio_br: t(f.dominio_br),
    dominio: t(f.dominio),
    dono_dominio: t(f.dono_dominio),
    dominio_validade: f.dominio_validade || null,
    classe: t(f.classe),
    funcao: t(f.funcao),
    tipo: t(f.tipo),
    obs: t(f.obs),
  }
  if (opts.editing) body.ativo = f.ativo
  if (f.senha) {
    if (f.senha !== opts.senhaSalva) body.senha = f.senha
  } else if (opts.editing && opts.limpar) {
    body.senha = null
  }
  return body
}

async function saveModal() {
  if (!form.value.nome.trim()) { saveErr.value = 'Informe o nome'; return }
  if (form.value.site.trim() && !siteValido(form.value.site)) { saveErr.value = MARCAS_ERROS.site_invalido; return }
  saving.value = true
  saveErr.value = null
  const m = modalEditing.value
  try {
    const body = buildBody(form.value, { editing: !!m, limpar: limparSenha.value, senhaSalva: senhaRevelada.value })
    if (m) {
      const updated = await api<MarcaOut>(`/api/marcas/${m.id}`, { method: 'PATCH', body })
      if (updated.ativo === !showInativas.value) applyRow(updated)
      else items.value = items.value.filter((x) => x.id !== m.id) // trocou de aba
      hideSenha(m.id)
    } else {
      const created = await api<MarcaOut>('/api/marcas', { method: 'POST', body })
      if (!showInativas.value) {
        items.value = [...items.value, created].sort((a, b) => a.nome.localeCompare(b.nome, 'pt-BR', { sensitivity: 'base' }))
      }
    }
    closeModal()
  } catch (e: any) {
    saveErr.value = apiErrMsg(e, MARCAS_ERROS)
  } finally {
    saving.value = false
  }
}

// =========================================================== logo (modal de edição)
// O logo não passa pelo form: o PUT multipart sobe na hora em que o arquivo é
// escolhido (spinner no botão) e a linha/modal recarregam com o MarcaOut que
// volta. O ui <Input> não suporta type=file, daí o <input> nativo escondido
// disparado pelo botão "Escolher logo" (padrão de logistica/chamados). Em
// "Nova marca" ainda não há id — o modal só avisa pra salvar antes.

// Mesmos limites do backend (routers/marcas.py: ≤ 1 MB, png/jpeg/gif pelos
// magic bytes) — checar aqui evita subir 5 MB pra receber um 413.
const LOGO_MAX_BYTES = 1_048_576
const LOGO_TIPOS = ['image/png', 'image/jpeg', 'image/gif']
const LOGO_ACCEPT = LOGO_TIPOS.join(',')

const logoInputRef = ref<HTMLInputElement | null>(null)
const logoBusy = ref(false)
const logoErr = ref<string | null>(null)

// null = pode subir; senão a mensagem (mesmos textos do MARCAS_ERROS). Tipo
// vazio (navegador não soube) passa — o backend valida pelos magic bytes.
function checkLogoFile(f: { size: number; type: string }): string | null {
  if (f.size > LOGO_MAX_BYTES) return MARCAS_ERROS.logo_too_large
  if (f.type && !LOGO_TIPOS.includes(f.type)) return MARCAS_ERROS.logo_tipo_invalido
  return null
}

function pickLogo() {
  if (logoBusy.value) return
  logoInputRef.value?.click()
}

function onLogoChange(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = '' // permite escolher o mesmo arquivo de novo
  if (file) void uploadLogo(file)
}

async function uploadLogo(file: File) {
  const m = modalEditing.value
  if (!m || logoBusy.value) return
  const msg = checkLogoFile(file)
  if (msg) { logoErr.value = msg; return }
  logoBusy.value = true
  logoErr.value = null
  try {
    const fd = new FormData()
    fd.append('file', file)
    const updated = await api<MarcaOut>(`/api/marcas/${m.id}/logo`, { method: 'PUT', body: fd })
    modalEditing.value = applyRow(updated)
  } catch (e: any) {
    logoErr.value = apiErrMsg(e, MARCAS_ERROS)
  } finally {
    logoBusy.value = false
  }
}

async function removeLogo() {
  const m = modalEditing.value
  if (!m || !m.has_logo || logoBusy.value) return
  if (!confirm(`Remover o logo de "${m.nome}"?`)) return
  logoBusy.value = true
  logoErr.value = null
  try {
    const updated = await api<MarcaOut>(`/api/marcas/${m.id}/logo`, { method: 'DELETE' })
    modalEditing.value = applyRow(updated)
  } catch (e: any) {
    logoErr.value = apiErrMsg(e, MARCAS_ERROS)
  } finally {
    logoBusy.value = false
  }
}
</script>

<template>
  <div class="space-y-4">
    <RouteTabs :tabs="TABS_CADASTROS" />
    <PageHeader
      title="Marcas"
      description="Marcas da operação: registro no INPI, domínios, empresa da assinatura, site e logo. Clique em uma célula para editar."
    >
      <template #actions>
        <Button size="sm" variant="ghost" :disabled="loading" @click="load">
          <RefreshCw class="size-4 mr-1" :class="{ 'animate-spin': loading }" /> recarregar
        </Button>
        <input
          v-model="search"
          placeholder="buscar marca, empresa, domínio…"
          class="border rounded px-2 py-1 text-sm bg-background w-56"
        />
        <Button
          size="sm"
          :variant="showInativas ? 'default' : 'ghost'"
          :disabled="loading"
          :title="showInativas ? 'Voltar às marcas ativas' : 'Ver marcas inativas'"
          @click="toggleInativas"
        >
          <Archive class="size-4 mr-1" /> {{ showInativas ? 'Ativas' : 'Inativas' }}
        </Button>
        <Button v-if="canEdit" size="sm" :disabled="showModal" @click="openNew">
          <Plus class="size-4 mr-1" /> Nova marca
        </Button>
      </template>
    </PageHeader>

    <div v-if="error" class="rounded border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive flex items-center gap-2">
      <AlertCircle class="h-4 w-4" /> {{ error }}
    </div>

    <div class="border rounded-lg overflow-auto max-h-[calc(100vh-220px)]">
      <table class="w-full text-sm border-collapse">
        <thead class="sticky top-0 bg-muted z-10">
          <tr>
            <th
              v-for="col in COLS"
              :key="col.key"
              class="text-left px-2 py-2 font-medium border-b border-border"
              :class="col.th"
            >{{ col.label }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading && !items.length">
            <td :colspan="COLS.length" class="text-center py-6 text-muted-foreground">
              <Loader2 class="inline h-4 w-4 animate-spin" /> carregando…
            </td>
          </tr>
          <tr v-else-if="!filtered.length">
            <td :colspan="COLS.length" class="text-center py-8 text-muted-foreground">
              nenhuma marca{{ showInativas ? ' inativa' : '' }}{{ search ? ' encontrada' : ' cadastrada' }}
            </td>
          </tr>

          <tr v-for="row in filtered" :key="row.id" class="hover:bg-accent/30">
            <template v-for="col in COLS" :key="col.key">
              <!-- texto editável inline (nome, site, classe, função, tipo, usuário, e-mail, domínios, dono, obs) -->
              <td
                v-if="col.kind === 'text'"
                class="border border-border px-2 py-1.5 text-xs"
                :class="[
                  col.truncate ? 'max-w-[240px]' : '',
                  canEdit ? 'cursor-pointer' : '',
                  {
                    'ring-2 ring-blue-500 ring-inset bg-background': isEditing(row.id, col.key),
                    'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(row.id, col.key),
                    'opacity-50': col.key === 'nome' && !row.ativo,
                  },
                ]"
                :title="col.truncate ? (cell(row, col.key) || '') : (col.key === 'nome' ? row.slug : undefined)"
                @click="!isEditing(row.id, col.key) && startEdit(row, col.key)"
              >
                <input
                  v-if="isEditing(row.id, col.key)"
                  :ref="setEditInputRef"
                  v-model="editValue" type="text"
                  class="w-full text-xs bg-transparent outline-none"
                  :placeholder="col.key === 'site' ? 'https://…' : undefined"
                  @blur="commitEdit" @keydown.enter.prevent="commitEdit" @keydown.escape.prevent="cancelEdit"
                />
                <!-- Marca: miniatura do logo antes do nome (só quando tem) -->
                <span v-else-if="col.key === 'nome'" class="flex items-center gap-1.5 font-medium">
                  <img
                    v-if="row.has_logo"
                    :src="logoSrc(row)"
                    :alt="`logo ${row.nome}`"
                    class="h-6 max-w-[64px] rounded object-contain shrink-0"
                  />
                  <span class="truncate">{{ row.nome }}</span>
                </span>
                <!-- Site: texto + ícone que abre o link sem entrar em edição -->
                <span v-else-if="col.key === 'site'" class="flex items-center gap-1 group">
                  <span class="truncate flex-1" :class="{ 'text-muted-foreground': !row.site }">{{ row.site || '—' }}</span>
                  <a
                    v-if="row.site"
                    :href="row.site"
                    target="_blank"
                    rel="noopener"
                    class="opacity-0 group-hover:opacity-100 shrink-0 p-0.5 hover:bg-muted rounded"
                    title="Abrir site"
                    @click.stop
                  >
                    <ExternalLink class="size-3 text-muted-foreground" />
                  </a>
                </span>
                <span
                  v-else
                  :class="{ 'block truncate': col.truncate, 'text-muted-foreground': !cell(row, col.key) }"
                >
                  {{ cell(row, col.key) || '—' }}
                </span>
              </td>

              <!-- Empresa da assinatura: só leitura (razão social); troca no modal -->
              <td
                v-else-if="col.kind === 'empresa'"
                class="border border-border px-2 py-1.5 text-xs max-w-[220px]"
                :class="{ 'cursor-pointer': canEdit }"
                :title="canEdit ? `${row.empresa_razao_social || 'sem empresa'} — editar no modal` : (row.empresa_razao_social || '')"
                @click="canEdit && openEdit(row)"
              >
                <span class="block truncate" :class="{ 'text-muted-foreground': !row.empresa_razao_social }">
                  {{ row.empresa_razao_social || '—' }}
                </span>
              </td>

              <!-- INPI: pill; com edit, o clique abre um <select> inline (store-info platform) -->
              <td
                v-else-if="col.kind === 'inpi'"
                class="border border-border px-2 py-1.5 text-xs"
                :class="{
                  'cursor-pointer': canEdit,
                  'ring-2 ring-blue-500 ring-inset bg-background': isEditing(row.id, 'inpi_status'),
                  'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(row.id, 'inpi_status'),
                }"
                @click="!isEditing(row.id, 'inpi_status') && startEdit(row, 'inpi_status')"
              >
                <select
                  v-if="isEditing(row.id, 'inpi_status')"
                  :ref="setEditInputRef"
                  v-model="editValue"
                  class="w-full text-xs bg-transparent outline-none"
                  @blur="commitEdit" @change="commitEdit" @keydown.escape.prevent="cancelEdit"
                >
                  <option v-for="s in INPI_STATUS" :key="s" :value="s">{{ INPI_STATUS_LABELS[s] }}</option>
                </select>
                <span v-else :class="inpiPill(row.inpi_status)">{{ inpiLabel(row.inpi_status) }}</span>
              </td>

              <!-- senha: nunca inline; olho/copiar só com edit (GET /senha exige edit) -->
              <td v-else-if="col.kind === 'senha'" class="border border-border px-2 py-1.5 text-xs">
                <div class="flex items-center gap-1 group">
                  <span
                    class="truncate flex-1 font-mono"
                    :class="{ 'text-muted-foreground': !row.has_senha }"
                  >
                    {{ row.has_senha ? (revealed.has(row.id) ? (revealedPasswords.get(row.id) || '••••') : '••••••••') : '—' }}
                  </span>
                  <button
                    v-if="row.has_senha && canEdit"
                    type="button"
                    class="opacity-0 group-hover:opacity-100 transition-opacity p-0.5 hover:bg-muted rounded"
                    :title="revealed.has(row.id) ? 'Ocultar' : 'Mostrar senha'"
                    @click="toggleReveal(row.id)"
                  >
                    <EyeOff v-if="revealed.has(row.id)" class="h-3 w-3" />
                    <Eye v-else class="h-3 w-3" />
                  </button>
                  <button
                    v-if="row.has_senha && canEdit"
                    type="button"
                    class="opacity-0 group-hover:opacity-100 transition-opacity p-0.5 hover:bg-muted rounded"
                    :title="copiedId === row.id ? 'Copiado!' : 'Copiar senha'"
                    @click="copySenha(row.id)"
                  >
                    <Check v-if="copiedId === row.id" class="h-3 w-3 text-emerald-600" />
                    <Copy v-else class="h-3 w-3" />
                  </button>
                </div>
              </td>

              <!-- validade do domínio: vermelho vencida, âmbar < 90 dias; edita no modal -->
              <td
                v-else-if="col.kind === 'validade'"
                class="border border-border px-2 py-1.5 text-xs whitespace-nowrap"
                :class="validadeClass(row.dominio_validade)"
                :title="row.dominio_validade ? `${daysUntil(row.dominio_validade)} dia(s)` : ''"
              >
                {{ fmtDate(row.dominio_validade) }}{{ validadeVencida(row.dominio_validade) ? ' (vencida)' : '' }}
              </td>

              <!-- ativo: botão-pill do SegmentRow (toggle PATCH {ativo}) -->
              <td v-else-if="col.kind === 'ativo'" class="border border-border px-2 py-1.5 text-center">
                <button
                  v-if="canEdit"
                  type="button"
                  class="px-2 py-0.5 rounded text-[11px] font-medium disabled:opacity-50"
                  :class="row.ativo
                    ? 'bg-emerald-100 dark:bg-emerald-900/40 text-emerald-700 dark:text-emerald-300'
                    : 'bg-muted text-muted-foreground'"
                  :disabled="ativoBusy.has(row.id)"
                  :title="row.ativo ? 'Inativar marca' : 'Reativar marca'"
                  @click="toggleAtivo(row)"
                >
                  {{ row.ativo ? 'ativa' : 'inativa' }}
                </button>
                <span v-else class="text-[11px]" :class="row.ativo ? 'text-emerald-600' : 'text-muted-foreground'">
                  {{ row.ativo ? 'sim' : 'não' }}
                </span>
              </td>

              <!-- ações: lápis abre o modal, lixeira exclui (cascateia nas redes sociais e nos e-mails) -->
              <td v-else-if="col.kind === 'acoes'" class="border border-border px-1 py-1 text-right whitespace-nowrap">
                <button
                  v-if="canEdit"
                  type="button"
                  class="p-1 text-muted-foreground hover:text-foreground hover:bg-muted rounded"
                  :title="`Editar ${row.nome}`"
                  @click="openEdit(row)"
                >
                  <Pencil class="h-3 w-3" />
                </button>
                <button
                  v-if="canDelete"
                  type="button"
                  class="p-1 text-destructive hover:bg-destructive/10 rounded"
                  :title="`Excluir ${row.nome}`"
                  @click="remove(row)"
                >
                  <Trash2 class="h-3 w-3" />
                </button>
              </td>
            </template>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- modal Nova marca / Editar marca -->
    <div v-if="showModal" class="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4" @click.self="closeModal">
      <div class="bg-background border rounded-lg w-full max-w-2xl p-5 space-y-4 max-h-[90vh] overflow-auto">
        <div class="flex items-center">
          <h2 class="text-lg font-semibold">{{ modalEditing ? 'Editar marca' : 'Nova marca' }}</h2>
          <Button class="ml-auto" size="sm" variant="ghost" @click="closeModal">
            <X class="size-4" />
          </Button>
        </div>

        <!-- Logo: sobe na hora (PUT multipart) — só depois da marca existir -->
        <div class="rounded-md border bg-muted/30 px-3 py-2">
          <div class="flex items-center gap-3">
            <Label class="shrink-0">Logo</Label>
            <template v-if="modalEditing">
              <img
                v-if="modalEditing.has_logo"
                :src="logoSrc(modalEditing)"
                :alt="`logo ${modalEditing.nome}`"
                class="h-12 max-w-[160px] rounded border bg-white object-contain"
              />
              <span v-else class="text-xs text-muted-foreground">sem logo</span>
              <input
                ref="logoInputRef"
                type="file"
                :accept="LOGO_ACCEPT"
                class="hidden"
                :disabled="logoBusy"
                @change="onLogoChange"
              />
              <Button size="sm" variant="outline" class="ml-auto" :disabled="logoBusy" @click="pickLogo">
                <Loader2 v-if="logoBusy" class="size-4 mr-1 animate-spin" />
                <Upload v-else class="size-4 mr-1" />
                {{ modalEditing.has_logo ? 'Trocar logo' : 'Escolher logo' }}
              </Button>
              <Button
                v-if="modalEditing.has_logo"
                size="sm"
                variant="ghost"
                class="text-destructive"
                :disabled="logoBusy"
                @click="removeLogo"
              >
                <Trash2 class="size-4 mr-1" /> Remover logo
              </Button>
            </template>
            <span v-else class="text-xs text-muted-foreground">salve a marca para enviar o logo</span>
          </div>
          <p class="text-[11px] text-muted-foreground mt-1">
            PNG, JPG ou GIF até 1 MB — usada na assinatura dos e-mails e na miniatura da lista.
          </p>
          <p v-if="logoErr" class="text-xs text-red-500 mt-1">erro: {{ logoErr }}</p>
        </div>

        <div class="grid grid-cols-2 gap-3">
          <div>
            <Label>Nome *</Label>
            <Input v-model="form.nome" required placeholder="poofy" @keydown.enter="saveModal" />
          </div>
          <div>
            <Label>INPI</Label>
            <select v-model="form.inpi_status" class="flex h-10 w-full rounded-md border border-input bg-background px-3 text-sm">
              <option v-for="s in INPI_STATUS" :key="s" :value="s">{{ INPI_STATUS_LABELS[s] }}</option>
            </select>
          </div>
          <div>
            <Label>Empresa da assinatura</Label>
            <select
              v-model="form.company_id"
              class="flex h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
              :disabled="empresasLoading"
              title="Razão social e CNPJ que assinam os e-mails automáticos da marca"
            >
              <option value="">— nenhuma —</option>
              <option v-for="c in empresas" :key="c.id" :value="c.id">{{ empresaLabel(c) }}</option>
            </select>
            <p v-if="empresasLoading" class="text-[11px] text-muted-foreground mt-1">carregando empresas…</p>
          </div>
          <div>
            <Label>Site</Label>
            <Input v-model="form.site" type="url" autocomplete="off" placeholder="https://www.marca.com.br" />
          </div>
          <div>
            <Label>Usuário</Label>
            <Input v-model="form.usuario" autocomplete="off" />
          </div>
          <div>
            <div class="flex items-center justify-between">
              <Label>Senha</Label>
              <button
                v-if="modalEditing?.has_senha && !limparSenha"
                type="button"
                class="text-[11px] text-muted-foreground hover:text-destructive underline underline-offset-2"
                title="Remove a senha salva ao salvar"
                @click="marcarLimparSenha"
              >
                limpar senha
              </button>
            </div>
            <div class="relative">
              <Input
                v-model="form.senha"
                type="text"
                autocomplete="off"
                data-1p-ignore
                data-lpignore="true"
                data-form-type="other"
                name="marca-secret"
                class="pr-9"
                :style="senhaVisible ? undefined : { WebkitTextSecurity: 'disc' }"
                :placeholder="modalEditing ? (limparSenha ? 'será removida ao salvar' : 'branco = manter') : ''"
              />
              <button
                type="button"
                class="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground disabled:opacity-50"
                :disabled="senhaRevealing"
                :title="senhaVisible ? 'ocultar' : 'ver senha salva'"
                @click="toggleSenha"
              >
                <Loader2 v-if="senhaRevealing" class="size-4 animate-spin" />
                <EyeOff v-else-if="senhaVisible" class="size-4" />
                <Eye v-else class="size-4" />
              </button>
            </div>
            <p v-if="limparSenha" class="text-[11px] text-amber-600 dark:text-amber-400 mt-1">
              a senha salva será removida ao salvar ·
              <button type="button" class="underline underline-offset-2" @click="limparSenha = false">desfazer</button>
            </p>
          </div>
          <div>
            <Label>E-mail</Label>
            <Input v-model="form.email" type="email" autocomplete="off" />
          </div>
          <div>
            <Label>Dono do domínio</Label>
            <Input v-model="form.dono_dominio" placeholder="omar, jessica…" />
          </div>
          <div>
            <Label>Domínio .br</Label>
            <Input v-model="form.dominio_br" placeholder="marca.com.br, marca.net.br" />
          </div>
          <div>
            <Label>Domínio</Label>
            <Input v-model="form.dominio" placeholder="marca.com" />
          </div>
          <div>
            <Label>Validade do domínio</Label>
            <Input v-model="form.dominio_validade" type="date" />
          </div>
          <div>
            <Label>Classe</Label>
            <Input v-model="form.classe" placeholder="ex-“atuação” da planilha" />
          </div>
          <div>
            <Label>Função</Label>
            <Input v-model="form.funcao" placeholder="locação celular, prc…" />
          </div>
          <div>
            <Label>Tipo</Label>
            <Input v-model="form.tipo" placeholder="cel eletro mala…" />
          </div>
        </div>
        <div>
          <Label>Observação</Label>
          <textarea v-model="form.obs" rows="2" class="w-full rounded-md border bg-background px-2 py-1.5 text-sm resize-y" />
        </div>

        <!-- Fone/e-mail das redes e do SAC: só informação — o dono é a aba Redes Sociais -->
        <p v-if="modalEditing" class="text-xs text-muted-foreground">
          Redes / SAC:
          <span :class="{ italic: !modalEditing.sac_fone }">{{ modalEditing.sac_fone ? fmtFone(modalEditing.sac_fone) : 'sem fone' }}</span>
          ·
          <span :class="{ italic: !modalEditing.sac_email }">{{ modalEditing.sac_email || 'sem e-mail' }}</span>
          ·
          <NuxtLink to="/redes-sociais" class="underline underline-offset-2 hover:text-foreground">editar em Redes Sociais</NuxtLink>
        </p>

        <label v-if="modalEditing" class="flex items-center gap-2 text-sm">
          <input v-model="form.ativo" type="checkbox" /> marca ativa
        </label>

        <div v-if="saveErr" class="text-sm text-red-500">erro: {{ saveErr }}</div>
        <div class="flex justify-end gap-2">
          <Button variant="ghost" :disabled="saving" @click="closeModal">cancelar</Button>
          <Button :disabled="saving || !form.nome.trim()" @click="saveModal">
            {{ saving ? 'salvando…' : (modalEditing ? 'Salvar' : 'Criar') }}
          </Button>
        </div>
      </div>
    </div>
  </div>
</template>
