<script setup lang="ts">
// Cadastros › Redes Sociais (Eduardo, 15/09/2026): grid no formato da aba
// r.social da planilha — uma linha por MARCA com fone/WhatsApp, usuário
// (e-mail do SAC) e senha compartilhados pelas redes, uma coluna por
// plataforma (as 5 da planilha, na ordem dela) com os @ das contas, e
// função/tipo/obs no fim. No banco continua uma linha por conta/plataforma —
// é o que a futura auto-postagem de vídeos vai consumir (models/marca.py).
//
// Dono das colunas da marca NESTA aba = recurso `redes_sociais` (v3.1):
// fone/usuário/senha, verificação do Zap, função, tipo e obs saem por
// PATCH /api/redes-sociais/marca/{marca_id} (MarcaSocialPatch) e a senha da
// marca é revelada por GET /api/redes-sociais/marca/{marca_id}/sac-senha —
// nunca por /api/marcas (a aba Marcas só lê fone/e-mail).
//
// Senha: NUNCA vem na listagem (só `has_sac_senha` / `has_senha_efetiva`).
// O plaintext só existe (a) na célula Senha enquanto o olho está aberto e
// (b) no modal da conta, revelado por clique via GET /{id}/senha (log no
// backend) — some sozinho em 30 s e é zerado ao recarregar/filtrar/fechar
// o modal/sair da página. POST/PATCH só levam a senha quando o usuário
// digitou uma (chave ausente = backend mantém, schemas/marcas.py); limpar é
// sempre um null explícito.
//
// Publicação automática (Eduardo, 15/09/2026): além da senha de LOGIN acima,
// cada conta pode ter uma CREDENCIAL DE PUBLICAÇÃO (token gerado no Business
// Suite) e o interruptor `postagem_auto`. O robô da aba Marketing › Criativos
// só publica sozinho onde os dois estão ligados — publicar na mão pela aba
// Criativos funciona de qualquer jeito. Os tetos por conta (posts/dia,
// intervalo mínimo) são opcionais: NULL = padrão do servidor (2 posts/dia,
// 90 min, config.py::marketing_postagem_*).
//
// O token é colado no sub-modal "conectar" (POST /{id}/conectar), é cifrado
// no servidor e NUNCA volta: a listagem só traz has_token / token_status /
// token_conta_externa / token_expires_at. Por isso o plaintext do token não
// é copiado pra nenhuma variável reativa nossa, não entra em log, :title,
// URL nem localStorage, e é zerado ao fechar o sub-modal / sair da página.
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { TABS_CADASTROS } from '~/lib/navGroups'
import {
  AlertCircle, BadgeCheck, Check, Copy, ExternalLink, Eye, EyeOff, Loader2, Pencil, Plug, Plus, RefreshCw, Send, Trash2, Unlink, X,
} from 'lucide-vue-next'
import {
  PLATAFORMAS, PLATAFORMA_LABELS, VERIFICACAO_GUIA, VERIFICACAO_LABELS, VERIFICACAO_STATUS, fmtFone, perfilUrl,
  type Plataforma, type VerificacaoStatus,
} from '~/lib/redesSociais'
import { apiErrMsg, MARCAS_ERROS } from '~/lib/apiError'

definePageMeta({
  middleware: ['permission'],
  permission: { resource: 'redes_sociais', action: 'view' },
})

// ---------- tipos (espelham app/schemas/marcas.py)

// MarcaRef: a linha da marca na pivot da planilha — sem login/e-mail/domínio
// do registro INPI, que ficam atrás da permissão `marcas`. Senha da marca só
// como `has_sac_senha`.
type MarcaRef = {
  id: string
  nome: string
  slug: string
  ativo: boolean
  classe: string | null
  funcao: string | null
  tipo: string | null
  obs: string | null
  sac_fone: string | null
  sac_email: string | null
  has_sac_senha: boolean
  whatsapp_verificacao_status: string
  whatsapp_verificacao_obs: string | null
  has_logo: boolean
  updated_at: string
}

type SenhaOrigem = 'conta' | 'marca' | null

// RedeSocialOut: `email`/`fone`/`has_senha` são os PRÓPRIOS da conta
// (override); `*_efetivo` já resolvem a herança da marca (NULL = herda).
type RedeSocialOut = {
  id: string
  marca_id: string
  marca_nome: string
  plataforma: string
  conta: string | null
  usuario: string | null
  url: string | null
  email: string | null
  fone: string | null
  has_senha: boolean
  email_efetivo: string | null
  fone_efetivo: string | null
  has_senha_efetiva: boolean
  senha_origem: SenhaOrigem
  verificacao_status: string
  verificacao_obs: string | null
  obs: string | null
  ativo: boolean
  // Publicação automática: interruptor + tetos DESTA conta (null = padrão
  // do servidor). O robô lê isso em services/marketing/postagens.py.
  postagem_auto: boolean
  postagem_max_dia: number | null
  postagem_intervalo_min: number | null
  // Estado da credencial de publicação (redes_sociais_tokens) — NUNCA o
  // token em si; `token_conta_externa` é o @/nome que ele autoriza.
  has_token: boolean
  token_status: string | null
  token_conta_externa: string | null
  token_expires_at: string | null
  created_at: string
  updated_at: string
}

// ContaExternaOut: o que o token enxerga (uma Página + a conta do Instagram
// ligada a ela). Só chega quando o backend NÃO conseguiu decidir sozinho —
// aí o operador escolhe e reenvia com `external_user_id`.
type ContaExterna = {
  page_id: string | null
  page_nome: string | null
  ig_user_id: string | null
  ig_username: string | null
}

// ConexaoOut: ok=true já gravou a credencial; ok=false = precisa escolher.
type ConexaoOut = {
  ok: boolean
  external_user_id: string | null
  external_username: string | null
  token_expires_at: string | null
  contas: ContaExterna[]
}

// cells tem chave pra toda plataforma (lista vazia = marca sem conta nela).
type GridRow = { marca: MarcaRef; cells: Record<string, RedeSocialOut[]> }
type Grid = { plataformas: string[]; rows: GridRow[] }

// Teto por conta: o <input type="number"> devolve number quando há valor e
// '' quando o campo está vazio — e vazio é justamente "herda o padrão".
type Teto = string | number

type Form = {
  marca_id: string
  plataforma: Plataforma
  conta: string
  usuario: string
  url: string
  email: string
  fone: string
  senha: string
  verificacao_status: VerificacaoStatus
  verificacao_obs: string
  ativo: boolean
  obs: string
  postagem_auto: boolean
  postagem_max_dia: Teto
  postagem_intervalo_min: Teto
}
type Modo = 'create' | 'edit'

// ---------- helpers puros (testados em tests/redes-sociais-sfc.cjs)

// Teto por conta (posts/dia, intervalo mínimo): campo vazio = herda o padrão
// do servidor, e isso vai como null EXPLÍCITO no PATCH — é o jeito de voltar
// ao padrão depois de ter fixado um número. Lixo digitado (0, negativo,
// texto) também vira null: melhor herdar o padrão do que mandar um teto sem
// sentido pro robô (Eduardo, 15/09/2026).
function tetoOuNull(v: Teto | null | undefined): number | null {
  const s = String(v ?? '').trim()
  if (!s) return null
  const n = Math.trunc(Number(s))
  return Number.isFinite(n) && n > 0 ? n : null
}

// Corpo do POST/PATCH da conta. `senha` só entra quando o usuário digitou
// uma E ela é diferente da revelada pelo olho (`senhaSalva`): chave ausente
// = backend mantém; reenviar o plaintext revelado seria tráfego à toa.
// `ativo` e os campos de postagem só no PATCH — RedeSocialCreate não tem
// esses campos (conta nova nasce ativa, sem token e sem postagem_auto).
// O TOKEN nunca passa por aqui: ele vai só no POST /{id}/conectar.
function montaBody(f: Form, modo: Modo, senhaSalva: string | null): Record<string, unknown> {
  const body: Record<string, unknown> = {
    marca_id: f.marca_id,
    plataforma: f.plataforma,
    conta: f.conta.trim() || null,
    usuario: f.usuario.trim() || null,
    url: f.url.trim() || null,
    email: f.email.trim() || null,
    fone: f.fone.trim() || null,
    verificacao_status: f.verificacao_status,
    verificacao_obs: f.verificacao_obs.trim() || null,
    obs: f.obs.trim() || null,
  }
  if (modo === 'edit') {
    body.ativo = f.ativo
    body.postagem_auto = f.postagem_auto
    body.postagem_max_dia = tetoOuNull(f.postagem_max_dia)
    body.postagem_intervalo_min = tetoOuNull(f.postagem_intervalo_min)
  }
  // Sem trim: espaço pode ser parte da senha (schemas/marcas.py::_senha).
  if (f.senha && f.senha !== senhaSalva) body.senha = f.senha
  return body
}

// O robô só publica sozinho quando a conta tem credencial E o interruptor
// ligado (postagens.py::pode_publicar_local) — é essa dupla que o
// indicadorzinho do grid mostra.
function postagemAutoOn(r: { postagem_auto?: boolean; has_token?: boolean }): boolean {
  return !!r.postagem_auto && !!r.has_token
}

// status da credencial (redes_sociais_tokens.status): ok | expirado | revogado.
const TOKEN_STATUS_LABELS: Record<string, string> = {
  ok: 'conectado',
  expirado: 'token vencido',
  revogado: 'token revogado',
}

type TokenEstado = {
  has_token?: boolean
  token_status?: string | null
  token_conta_externa?: string | null
} | null | undefined

// Pill do estado da credencial no modal: nunca o token, só o @/nome que ele
// autoriza (token_conta_externa) e o status quando não está ok.
function tokenPillTexto(r: TokenEstado): string {
  if (!r?.has_token) return 'sem token'
  const conta = (r.token_conta_externa || '').trim().replace(/^@+/, '')
  const base = conta ? `conectado como @${conta}` : 'conectado'
  const st = r.token_status || 'ok'
  return st === 'ok' ? base : `${base} · ${TOKEN_STATUS_LABELS[st] || st}`
}

// Verde só quando dá pra publicar; âmbar quando venceu/foi revogado (o robô
// para sozinho); cinza sem credencial.
function tokenPillClass(r: TokenEstado): string {
  if (!r?.has_token) return 'bg-muted text-muted-foreground'
  return (r.token_status || 'ok') === 'ok'
    ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300'
    : 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300'
}

// Escolha do operador quando o backend não consegue decidir: o valor do
// rádio é o id que o `external_user_id` espera — ig_user_id no Instagram,
// page_id nas Páginas (routers/redes_sociais.py::conectar_conta).
function contaExternaId(c: ContaExterna, plataforma: string): string {
  const id = plataforma === 'instagram' ? c.ig_user_id : c.page_id
  return (id || '').trim()
}

// Rótulo do rádio: o que o operador reconhece (nome da Página e o @ do IG).
function contaExternaLabel(c: ContaExterna): string {
  const partes: string[] = []
  if (c.page_nome) partes.push(c.page_nome)
  if (c.ig_username) partes.push(`@${String(c.ig_username).replace(/^@+/, '')}`)
  if (partes.length) return partes.join(' · ')
  return c.page_id || c.ig_user_id || 'conta sem nome'
}

// Tooltip do chip: contato EFETIVO (marcado "da marca" quando herdado) e o
// andamento da verificação — nunca a senha, só de onde ela vem.
function chipTitle(r: RedeSocialOut): string {
  const partes: string[] = [PLATAFORMA_LABELS[r.plataforma as Plataforma] || r.plataforma]
  if (r.usuario) partes.push(`login: ${r.usuario}`)
  if (r.email_efetivo) partes.push(r.email ? r.email_efetivo : `${r.email_efetivo} (da marca)`)
  if (r.fone_efetivo) partes.push(`fone: ${fmtFone(r.fone_efetivo)}${r.fone ? '' : ' (da marca)'}`)
  if (r.verificacao_status && r.verificacao_status !== 'nao_solicitado') {
    partes.push(VERIFICACAO_LABELS[r.verificacao_status as VerificacaoStatus] || r.verificacao_status)
  }
  if (r.verificacao_obs) partes.push(r.verificacao_obs)
  if (!r.ativo) partes.push('inativa')
  if (r.senha_origem === 'conta') partes.push('senha própria')
  else if (r.senha_origem === 'marca') partes.push('senha da marca')
  // Postagem automática: o tooltip diz por que o pontinho do Send aparece —
  // e avisa quando o interruptor está ligado mas falta credencial (o robô
  // não publica assim).
  if (r.postagem_auto) partes.push(r.has_token ? 'postagem automática' : 'postagem automática (sem token)')
  if (r.obs) partes.push(r.obs)
  return partes.join(' · ')
}

// Busca: marca (nome/slug/e-mail SAC/fone) ou qualquer conta/e-mail da linha.
function rowMatches(row: GridRow, qRaw: string): boolean {
  // Os chips mostram "@conta" mas `conta` é gravada sem "@": busca "@poofy"
  // tem que achar "poofy".
  const q = qRaw.trim().toLowerCase().replace(/^@+/, '')
  if (!q) return true
  const m = row.marca
  if ([m.nome, m.slug, m.sac_email, m.sac_fone].some((v) => (v || '').toLowerCase().includes(q))) return true
  return Object.values(row.cells).some((lista) =>
    lista.some((r) => [r.conta, r.email, r.email_efetivo].some((v) => (v || '').toLowerCase().includes(q))),
  )
}

// Depois de editar a linha da marca (e-mail/fone/senha) os `*_efetivo` das
// contas que herdam ficariam velhos até o próximo recarregar — recalcula
// aqui com a MESMA regra de routers/redes_sociais.py::rede_out.
function sincronizaEfetivos(row: GridRow): void {
  const m = row.marca
  for (const lista of Object.values(row.cells)) {
    for (const r of lista) {
      r.email_efetivo = r.email || m.sac_email
      r.fone_efetivo = r.fone || m.sac_fone
      r.senha_origem = r.has_senha ? 'conta' : m.has_sac_senha ? 'marca' : null
      r.has_senha_efetiva = r.senha_origem !== null
    }
  }
}

// Pontinho colorido do status de verificação (WhatsApp da marca e chips das
// contas): âmbar em andamento, vermelho recusado, verde verificado.
const VERIFICACAO_DOT: Record<string, string> = {
  nao_solicitado: 'bg-muted-foreground/30',
  em_andamento: 'bg-amber-500',
  verificado: 'bg-emerald-500',
  recusado: 'bg-red-500',
}
function dotClass(status: string | null | undefined): string {
  return VERIFICACAO_DOT[status || 'nao_solicitado'] || VERIFICACAO_DOT.nao_solicitado
}

// Texto curto ao lado do fone (a coluna tem 130px): nada quando não
// solicitado — o pontinho apagado já diz.
const VERIFICACAO_CURTO: Record<string, string> = {
  nao_solicitado: '',
  em_andamento: 'andam.',
  verificado: 'verif.',
  recusado: 'recus.',
}

function verifLabel(status: string | null | undefined): string {
  return VERIFICACAO_LABELS[(status || 'nao_solicitado') as VerificacaoStatus] || status || ''
}

// Dica do bloco de senha do modal da conta, dirigida por `senha_origem`
// (spec v3.1): herdada → convida a digitar uma própria; própria → avisa; sem
// nenhuma → diz. Quando o olho revelou, informa de onde veio a senha.
function senhaHintTexto(o: {
  modo: Modo
  origem: SenhaOrigem
  digitada: boolean
  marcaTemSenha: boolean
  revelada: SenhaOrigem
}): string {
  if (o.digitada) return 'senha própria desta conta — grava ao Salvar'
  if (o.modo === 'create') return o.marcaTemSenha ? 'em branco herda a senha da marca' : 'opcional'
  let base: string
  if (o.origem === 'conta') base = 'senha própria desta conta'
  else if (o.origem === 'marca') base = 'herdada da marca — digite para usar uma senha própria'
  else base = 'sem senha — nem na conta nem na marca'
  if (o.revelada) base += ` · mostrando a senha ${o.revelada === 'marca' ? 'da marca' : 'da conta'}`
  return base
}

// ---------- fim helpers puros

const { api } = useApi()
// Todas as colunas da marca nesta aba (fone/usuário/senha/verificação/
// função/tipo/obs) e as contas: `redes_sociais:edit` (v3.1).
const canEdit = useCan('redes_sociais', 'edit')
const canDelete = useCan('redes_sociais', 'delete')

// Colunas de texto da marca depois das plataformas (ordem da planilha).
// `truncate` = célula compacta com o texto inteiro no title.
const MARCA_TEXT_COLS: { key: 'funcao' | 'tipo' | 'obs'; label: string; th: string; truncate?: string }[] = [
  { key: 'funcao', label: 'Função', th: 'min-w-[80px]' },
  { key: 'tipo', label: 'Tipo', th: 'min-w-[80px]' },
  { key: 'obs', label: 'Obs', th: 'min-w-[100px]', truncate: 'max-w-[150px]' },
]

// =============================================== senha da marca (célula)
// marcas.vue / store-info.vue:725-762, com os cuidados da SPEC v2: revela
// só no clique, esconde sozinha em 30 s, e tudo some em recarregar/filtro/
// onUnmounted. Declarado ANTES do load() porque ele chama clearRevealed()
// já na carga inicial (top-level await).

const REVEAL_TTL_MS = 30_000
const revealed = ref<Set<string>>(new Set())
const revealedSenhas = ref<Map<string, string>>(new Map())
const revealTimers = new Map<string, ReturnType<typeof setTimeout>>()

function hideRevealed(id: string) {
  revealed.value.delete(id)
  revealedSenhas.value.delete(id)
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
  revealedSenhas.value.clear()
}

async function toggleReveal(marcaId: string) {
  if (revealed.value.has(marcaId)) {
    hideRevealed(marcaId)
    return
  }
  try {
    const r = await api<{ senha: string }>(`/api/redes-sociais/marca/${marcaId}/sac-senha`)
    revealedSenhas.value.set(marcaId, r.senha)
    revealed.value.add(marcaId)
    revealTimers.set(marcaId, setTimeout(() => hideRevealed(marcaId), REVEAL_TTL_MS))
  } catch (e: any) {
    error.value = apiErrMsg(e, MARCAS_ERROS)
  }
}

const copiedId = ref<string | null>(null)
async function copySacSenha(marcaId: string) {
  // Se não está revelada, busca e escreve direto no clipboard — sem guardar.
  let pw = revealedSenhas.value.get(marcaId)
  if (pw == null) {
    try {
      pw = (await api<{ senha: string }>(`/api/redes-sociais/marca/${marcaId}/sac-senha`)).senha
    } catch (e: any) {
      error.value = apiErrMsg(e, MARCAS_ERROS)
      return
    }
  }
  try {
    await navigator.clipboard.writeText(pw ?? '')
    copiedId.value = marcaId
    setTimeout(() => { if (copiedId.value === marcaId) copiedId.value = null }, 1200)
  } catch { /* clipboard indisponível */ }
}

// ====================================================================== grid

const grid = ref<Grid | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)
const search = ref('')
const showGuia = ref(false)

// Colunas vêm da constante web (mesma ordem do enum); a lista da API só
// serve pra avisar divergência no console — sem quebrar a tela.
let plataformasAvisado = false
function avisaDivergencia(daApi: string[]) {
  if (plataformasAvisado) return
  const a = daApi.join(',')
  const b = PLATAFORMAS.join(',')
  if (a === b) return
  plataformasAvisado = true
  console.warn(`[redes-sociais] plataformas da API (${a}) ≠ lib/redesSociais.ts (${b}) — atualizar o lib`)
}

// Marca | Fone | Usuário | Senha | plataformas… | Função | Tipo | Obs
const colspan = computed(() => 4 + PLATAFORMAS.length + MARCA_TEXT_COLS.length)

async function load() {
  loading.value = true
  error.value = null
  // Recarregar descarta qualquer senha revelada (SPEC v2) — na célula e no
  // modal — e o token que estivesse colado no sub-modal de conectar.
  clearRevealed()
  hideSenhaModal()
  fecharConexao()
  try {
    const g = await api<Grid>('/api/redes-sociais/grid')
    avisaDivergencia(g.plataformas)
    grid.value = g
  } catch (e: any) {
    error.value = apiErrMsg(e, MARCAS_ERROS)
  } finally {
    loading.value = false
  }
}

// "Só com contas" (Eduardo, 15/09): a planilha tem 4 marcas em r.social;
// por padrão a grade mostra só marcas que já têm alguma conta/linha. O botão
// "Todas as marcas" traz as demais (é lá que se usa o "+" pra começar uma).
const soComContas = ref(true)
function temConta(r: GridRow): boolean {
  return Object.values(r.cells).some((lista) => lista.length > 0)
}
const filteredRows = computed<GridRow[]>(() => {
  if (!grid.value) return []
  const q = search.value.trim().toLowerCase()
  let rows = grid.value.rows
  // Busca ignora o filtro: quem procura "12paxion" quer achar a marca.
  if (q) return rows.filter((r) => rowMatches(r, q))
  if (soComContas.value) rows = rows.filter(temConta)
  return rows
})

// Opções do select de marca no modal — inativas entram marcadas.
const marcasOpcoes = computed(() =>
  (grid.value?.rows || []).map((r) => ({
    id: r.marca.id,
    label: r.marca.ativo ? r.marca.nome : `${r.marca.nome} (inativa)`,
  })),
)

// Miniatura do logo só quando has_logo; `v=updated_at` fura o cache
// (Cache-Control private, max-age=300) depois de trocar o logo em Marcas.
function logoSrc(m: MarcaRef): string {
  return `/api/marcas/${m.id}/logo?v=${encodeURIComponent(m.updated_at)}`
}

function rowDaMarca(marcaId: string): GridRow | undefined {
  return grid.value?.rows.find((r) => r.marca.id === marcaId)
}

// Resposta do PATCH /marca/{id} (MarcaRef) → atualiza a linha sem recarregar
// o grid; as contas que herdam recebem os efetivos novos; senha revelada da
// marca some (pode ter mudado).
function aplicaMarca(upd: MarcaRef) {
  const row = rowDaMarca(upd.id)
  if (!row) return
  Object.assign(row.marca, upd)
  sincronizaEfetivos(row)
  hideRevealed(upd.id)
}

// ================================================ edição inline (linha da marca)
// Padrão store-info.vue/marcas.vue: clique simples abre input na célula
// (ring azul), Enter/blur salva PATCH parcial em /api/redes-sociais/marca/
// {id}, Esc cancela, flash verde 1,2 s. Senha NUNCA inline (mini-modal).

type MarcaInlineField =
  | 'sac_fone' | 'sac_email' | 'funcao' | 'tipo' | 'obs'
  | 'whatsapp_verificacao_status' | 'whatsapp_verificacao_obs'

const cellEdit = ref<{ id: string; field: MarcaInlineField } | null>(null)
const editValue = ref('')
const editOriginal = ref('')
const editInputRef = ref<HTMLInputElement | HTMLSelectElement | null>(null)
function setEditInputRef(el: any) {
  if (el) editInputRef.value = el
}
const flashed = ref<Set<string>>(new Set())

function isEditing(id: string, field: MarcaInlineField) {
  return cellEdit.value?.id === id && cellEdit.value?.field === field
}

function isFlashed(id: string, field: MarcaInlineField) {
  return flashed.value.has(`${id}::${field}`)
}

function flash(id: string, field: MarcaInlineField) {
  const k = `${id}::${field}`
  flashed.value.add(k)
  setTimeout(() => flashed.value.delete(k), 1200)
}

// Classes da célula inline (cursor no hover com edit, ring quando editando,
// flash verde ao salvar, apagado quando vazio).
function inlineClass(id: string, field: MarcaInlineField, vazio: boolean) {
  const editing = isEditing(id, field)
  return {
    'cursor-pointer hover:bg-accent/30 rounded': canEdit.value && !editing,
    'ring-2 ring-blue-500 ring-inset bg-background': editing,
    'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(id, field),
    'text-muted-foreground': vazio && !editing,
  }
}

async function startEdit(row: GridRow, field: MarcaInlineField) {
  if (!canEdit.value || isEditing(row.marca.id, field)) return
  cellEdit.value = { id: row.marca.id, field }
  // Fone aparece formatado; o backend guarda só dígitos (_digitos), então
  // mandar "(11) 98888-7777" de volta é seguro.
  const raw = field === 'sac_fone' ? fmtFone(row.marca.sac_fone) : row.marca[field]
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
  cellEdit.value = null
  editValue.value = ''
  editOriginal.value = ''
}

async function commitEdit() {
  if (!cellEdit.value) return
  const { id, field } = cellEdit.value
  const raw = String(editValue.value ?? '').trim()
  const changed = editValue.value !== editOriginal.value
  // Fecha o input ANTES do PATCH: o <select> do status dispara change E blur
  // (e o input, Enter E blur) — se o estado ficasse aberto até a resposta,
  // o segundo evento mandaria um PATCH repetido (marcas.vue).
  cancelEdit()
  if (!changed) return
  if (!rowDaMarca(id)) return

  const payload: Record<string, unknown> = {}
  if (field === 'whatsapp_verificacao_status') {
    // Coluna NOT NULL — vazio não salva.
    if (!raw) return
    payload.whatsapp_verificacao_status = raw
  } else {
    payload[field] = raw || null
  }

  try {
    const upd = await api<MarcaRef>(`/api/redes-sociais/marca/${id}`, { method: 'PATCH', body: payload })
    aplicaMarca(upd)
    flash(id, field)
  } catch (e: any) {
    error.value = apiErrMsg(e, MARCAS_ERROS)
  }
}

function zapTitle(m: MarcaRef): string {
  const partes = [`verificação do WhatsApp: ${verifLabel(m.whatsapp_verificacao_status)}`]
  if (m.whatsapp_verificacao_obs) partes.push(m.whatsapp_verificacao_obs)
  if (canEdit.value) partes.push('clique para alterar')
  return partes.join(' — ')
}

// ================================================ senha da marca (mini-modal)
// "Senha das redes/SAC": input mascarado (padrão nf-cadastros.vue) → PATCH
// {sac_senha}; "limpar" → PATCH {sac_senha: null}. O olho revela a salva
// via GET /sac-senha quando o campo está vazio. Tudo zerado ao fechar.

const senhaMarca = ref<{ marca: MarcaRef } | null>(null)
const senhaMarcaValor = ref('')
const senhaMarcaVisible = ref(false)
const senhaMarcaRevealing = ref(false)
const senhaMarcaSaving = ref(false)
const senhaMarcaErr = ref<string | null>(null)
// O que o olho trouxe do backend: se o campo continua igual no Salvar, não
// reenvia (evita re-cifrar e trafegar a senha à toa).
const senhaMarcaSalva = ref<string | null>(null)

function openSenhaMarca(m: MarcaRef) {
  if (!canEdit.value) return
  senhaMarcaValor.value = ''
  senhaMarcaVisible.value = false
  senhaMarcaSalva.value = null
  senhaMarcaErr.value = null
  senhaMarca.value = { marca: m }
}

function closeSenhaMarca() {
  senhaMarcaValor.value = ''
  senhaMarcaVisible.value = false
  senhaMarcaSalva.value = null
  senhaMarcaErr.value = null
  senhaMarca.value = null
}

async function toggleSenhaMarca() {
  if (senhaMarcaVisible.value) {
    senhaMarcaVisible.value = false
    return
  }
  const m = senhaMarca.value?.marca
  if (m?.has_sac_senha && !senhaMarcaValor.value && canEdit.value) {
    senhaMarcaRevealing.value = true
    try {
      const r = await api<{ senha: string }>(`/api/redes-sociais/marca/${m.id}/sac-senha`)
      // Modal fechado/trocado durante a chamada: não vaza pra outro form.
      if (senhaMarca.value?.marca.id !== m.id) return
      senhaMarcaValor.value = r.senha || ''
      senhaMarcaSalva.value = r.senha || ''
    } catch (e: any) {
      senhaMarcaErr.value = apiErrMsg(e, MARCAS_ERROS)
      return
    } finally {
      senhaMarcaRevealing.value = false
    }
  }
  senhaMarcaVisible.value = true
}

async function saveSenhaMarca() {
  const m = senhaMarca.value?.marca
  if (!m || !canEdit.value) return
  const nova = senhaMarcaValor.value
  if (!nova) {
    senhaMarcaErr.value = 'digite a senha — ou use "limpar senha"'
    return
  }
  // Revelada e não alterada: nada a salvar.
  if (nova === senhaMarcaSalva.value) {
    closeSenhaMarca()
    return
  }
  senhaMarcaSaving.value = true
  senhaMarcaErr.value = null
  try {
    const upd = await api<MarcaRef>(`/api/redes-sociais/marca/${m.id}`, { method: 'PATCH', body: { sac_senha: nova } })
    aplicaMarca(upd)
    closeSenhaMarca()
  } catch (e: any) {
    senhaMarcaErr.value = apiErrMsg(e, MARCAS_ERROS)
  } finally {
    senhaMarcaSaving.value = false
  }
}

async function limparSenhaMarca() {
  const m = senhaMarca.value?.marca
  if (!m?.has_sac_senha || !canEdit.value) return
  if (!confirm(`Apagar a senha das redes/SAC de ${m.nome}?\n\nAs contas que herdam essa senha ficam sem senha.`)) return
  senhaMarcaSaving.value = true
  senhaMarcaErr.value = null
  try {
    const upd = await api<MarcaRef>(`/api/redes-sociais/marca/${m.id}`, { method: 'PATCH', body: { sac_senha: null } })
    aplicaMarca(upd)
    closeSenhaMarca()
  } catch (e: any) {
    senhaMarcaErr.value = apiErrMsg(e, MARCAS_ERROS)
  } finally {
    senhaMarcaSaving.value = false
  }
}

// ============================================================ modal da conta
// Criar e editar usam o mesmo formulário; `modal.rede` = null é criação.

const modal = ref<{ rede: RedeSocialOut | null } | null>(null)
const form = ref<Form>(emptyForm())
const saving = ref(false)
const modalErr = ref<string | null>(null)

function emptyForm(marcaId = '', plataforma: Plataforma = PLATAFORMAS[0]): Form {
  return {
    marca_id: marcaId,
    plataforma,
    conta: '',
    usuario: '',
    url: '',
    email: '',
    fone: '',
    senha: '',
    verificacao_status: 'nao_solicitado',
    verificacao_obs: '',
    ativo: true,
    obs: '',
    // Conta nova nasce sem publicação automática: o bloco só aparece na
    // edição, porque conectar a credencial precisa da conta já salva.
    postagem_auto: false,
    postagem_max_dia: '',
    postagem_intervalo_min: '',
  }
}

const modo = computed<Modo>(() => (modal.value?.rede ? 'edit' : 'create'))
const plataformaLabel = computed(() => PLATAFORMA_LABELS[form.value.plataforma] || form.value.plataforma)
// Marca escolhida no form — dá os placeholders "herda: …" e diz se há senha
// da marca pra herdar.
const marcaDoForm = computed<MarcaRef | null>(() => rowDaMarca(form.value.marca_id)?.marca ?? null)
const emailPlaceholder = computed(() =>
  marcaDoForm.value?.sac_email ? `herda: ${marcaDoForm.value.sac_email}` : 'sem e-mail na marca — informe o da conta',
)
const fonePlaceholder = computed(() =>
  marcaDoForm.value?.sac_fone ? `herda: ${fmtFone(marcaDoForm.value.sac_fone)}` : 'só dígitos',
)
// Sugestão de link a partir do @ — placeholder quando `url` está vazio; o
// ícone abre o que estiver preenchido, senão a sugestão.
const urlSugerida = computed(() => perfilUrl(form.value.plataforma, form.value.conta.trim()))
const urlAbrir = computed(() => form.value.url.trim() || urlSugerida.value)

function openCreate(marcaId = '', plataforma: Plataforma = PLATAFORMAS[0]) {
  if (!canEdit.value) return
  hideSenhaModal()
  fecharConexao()
  form.value = emptyForm(marcaId, plataforma)
  modalErr.value = null
  modal.value = { rede: null }
}

function openEdit(r: RedeSocialOut) {
  hideSenhaModal()
  fecharConexao()
  form.value = {
    marca_id: r.marca_id,
    plataforma: r.plataforma as Plataforma,
    conta: r.conta || '',
    usuario: r.usuario || '',
    url: r.url || '',
    // Só os PRÓPRIOS da conta — vazio = herda (placeholder mostra o da marca).
    email: r.email || '',
    fone: r.fone || '',
    // Senha nunca vem na listagem — campo começa vazio ("branco = manter").
    senha: '',
    verificacao_status: (VERIFICACAO_STATUS.includes(r.verificacao_status as VerificacaoStatus)
      ? r.verificacao_status
      : 'nao_solicitado') as VerificacaoStatus,
    verificacao_obs: r.verificacao_obs || '',
    ativo: r.ativo,
    obs: r.obs || '',
    postagem_auto: !!r.postagem_auto,
    // null = herda o padrão do servidor → campo vazio (o placeholder diz qual é).
    postagem_max_dia: r.postagem_max_dia == null ? '' : r.postagem_max_dia,
    postagem_intervalo_min: r.postagem_intervalo_min == null ? '' : r.postagem_intervalo_min,
  }
  modalErr.value = null
  modal.value = { rede: r }
}

function closeModal() {
  // Zera o formulário inteiro (inclusive senha e o token colado) ao fechar — spec v2.
  hideSenhaModal()
  fecharConexao()
  form.value = emptyForm()
  modalErr.value = null
  modal.value = null
}

async function saveRede() {
  if (!modal.value || !canEdit.value) return
  if (!form.value.marca_id) {
    modalErr.value = 'escolha a marca'
    return
  }
  saving.value = true
  modalErr.value = null
  try {
    const rede = modal.value.rede
    const body = montaBody(form.value, modo.value, senhaSalva.value)
    if (rede) await api(`/api/redes-sociais/${rede.id}`, { method: 'PATCH', body })
    else await api('/api/redes-sociais', { method: 'POST', body })
    closeModal()
    await load()
  } catch (e: any) {
    modalErr.value = apiErrMsg(e, MARCAS_ERROS)
  } finally {
    saving.value = false
  }
}

async function removeRede() {
  const rede = modal.value?.rede
  if (!rede || !canDelete.value) return
  const nome = rede.conta ? `@${rede.conta}` : 'esta linha sem conta'
  if (!confirm(`Excluir ${nome} (${PLATAFORMA_LABELS[rede.plataforma as Plataforma] || rede.plataforma}) de ${rede.marca_nome}?`)) return
  saving.value = true
  modalErr.value = null
  try {
    await api(`/api/redes-sociais/${rede.id}`, { method: 'DELETE' })
    closeModal()
    await load()
  } catch (e: any) {
    modalErr.value = apiErrMsg(e, MARCAS_ERROS)
  } finally {
    saving.value = false
  }
}

// ------------------------------------------------------------- senha (modal)
// Padrão nf-cadastros.vue: <Input type="text"> mascarado por CSS
// (WebkitTextSecurity) + autocomplete off / data-1p-ignore pra não acionar o
// gerenciador de senhas do navegador. O olho revela a senha EFETIVA (própria
// ou herdada da marca — GET /{id}/senha devolve `origem`) só quando o campo
// está vazio. `senhaSalva` guarda o revelado só pra não reenviar no PATCH;
// ocultar (clique, 30 s, fechar, sair da página) descarta o plaintext.

const senhaVisible = ref(false)
const senhaRevealing = ref(false)
const senhaSalva = ref<string | null>(null)
const senhaOrigemRevelada = ref<SenhaOrigem>(null)
const senhaCopiada = ref(false)
let senhaTimer: ReturnType<typeof setTimeout> | null = null

function armaOcultar() {
  if (senhaTimer) clearTimeout(senhaTimer)
  senhaTimer = setTimeout(hideSenhaModal, 30_000)
}

function hideSenhaModal() {
  if (senhaTimer) {
    clearTimeout(senhaTimer)
    senhaTimer = null
  }
  senhaVisible.value = false
  // Descarta o plaintext revelado — mas não apaga o que o usuário digitou
  // por cima (aí é senha nova, vai no PATCH).
  if (senhaSalva.value !== null && form.value.senha === senhaSalva.value) form.value.senha = ''
  senhaSalva.value = null
  senhaOrigemRevelada.value = null
}

async function toggleSenha() {
  if (senhaVisible.value) {
    hideSenhaModal()
    return
  }
  const rede = modal.value?.rede
  if (rede?.has_senha_efetiva && !form.value.senha && canEdit.value) {
    senhaRevealing.value = true
    try {
      const r = await api<{ senha: string; origem: SenhaOrigem }>(`/api/redes-sociais/${rede.id}/senha`)
      // Modal trocado/fechado durante a chamada: não vaza a senha pra outro
      // formulário.
      if (modal.value?.rede?.id !== rede.id) return
      form.value.senha = r.senha || ''
      senhaSalva.value = r.senha || ''
      senhaOrigemRevelada.value = r.origem ?? null
    } catch (e: any) {
      modalErr.value = apiErrMsg(e, MARCAS_ERROS)
      return
    } finally {
      senhaRevealing.value = false
    }
  }
  senhaVisible.value = true
  armaOcultar()
}

// Copiar: usa o que está no campo; se vazio, busca a efetiva e escreve
// direto na área de transferência sem guardar (store-info.vue::copyPassword).
async function copySenha() {
  if (!canEdit.value) return
  let pw = form.value.senha
  if (!pw) {
    const rede = modal.value?.rede
    if (!rede?.has_senha_efetiva) return
    try {
      pw = (await api<{ senha: string }>(`/api/redes-sociais/${rede.id}/senha`)).senha
    } catch (e: any) {
      modalErr.value = apiErrMsg(e, MARCAS_ERROS)
      return
    }
  }
  try {
    await navigator.clipboard.writeText(pw ?? '')
    senhaCopiada.value = true
    setTimeout(() => { senhaCopiada.value = false }, 1200)
  } catch { /* clipboard indisponível */ }
}

// "voltar a herdar a da marca": só quando a conta tem senha PRÓPRIA
// (senha_origem 'conta'). PATCH explícito { senha: null } — o Salvar nunca
// limpa (campo vazio = manter). Com senha herdada não há o que limpar.
async function voltarHerdarSenha() {
  const rede = modal.value?.rede
  if (rede?.senha_origem !== 'conta' || !canEdit.value) return
  if (!confirm('Apagar a senha própria desta conta e voltar a usar a senha da marca?')) return
  saving.value = true
  modalErr.value = null
  try {
    const upd = await api<RedeSocialOut>(`/api/redes-sociais/${rede.id}`, { method: 'PATCH', body: { senha: null } })
    hideSenhaModal()
    form.value.senha = ''
    if (modal.value) modal.value = { rede: upd }
    const row = rowDaMarca(upd.marca_id)
    const lista = row?.cells[upd.plataforma]
    const i = lista ? lista.findIndex((x) => x.id === upd.id) : -1
    if (lista && i >= 0) lista[i] = upd
  } catch (e: any) {
    modalErr.value = apiErrMsg(e, MARCAS_ERROS)
  } finally {
    saving.value = false
  }
}

const senhaHint = computed(() =>
  senhaHintTexto({
    modo: modo.value,
    origem: modal.value?.rede?.senha_origem ?? null,
    digitada: !!form.value.senha && form.value.senha !== senhaSalva.value,
    marcaTemSenha: !!marcaDoForm.value?.has_sac_senha,
    revelada: senhaOrigemRevelada.value,
  }),
)

const senhaPlaceholder = computed(() => {
  const rede = modal.value?.rede
  if (!rede) return marcaDoForm.value?.has_sac_senha ? 'em branco herda a senha da marca' : ''
  if (rede.senha_origem === 'conta') return 'branco = manter a própria'
  if (rede.senha_origem === 'marca') return 'herda a senha da marca'
  return ''
})

// ============================================== credencial de publicação (token)
// Sub-modal "conectar" do bloco Publicação automática. O operador cola o
// token do Business Suite e ele vai DIRETO pro POST /{id}/conectar: o
// plaintext só existe no v-model do campo enquanto o sub-modal está aberto
// (é preciso pra reenviar quando o backend devolve ok=false pedindo a
// escolha da conta) e é zerado ao fechar / salvar / sair da página. Nada de
// log, title, URL ou localStorage — o backend também não devolve o token
// em endpoint nenhum (Eduardo, 15/09/2026).

// Códigos novos do /conectar. Ficam aqui (e não em lib/apiError.ts) porque
// são só desta tela; se virarem de mais alguém, sobem pro MARCAS_ERROS.
const CONEXAO_ERROS: Record<string, string> = {
  ...MARCAS_ERROS,
  token_recusado_pela_meta: 'A Meta recusou esse token — gere um novo no Business Suite e cole de novo',
  token_invalido: 'Token inválido — cole o token inteiro, sem espaços',
}

const conexao = ref<{ rede: RedeSocialOut } | null>(null)
const tokenValor = ref('')
const tokenVisible = ref(false)
const conectando = ref(false)
const conexaoErr = ref<string | null>(null)
// Só quando o backend não consegue decidir sozinho (ok=false): o que o token
// enxerga vira opção de rádio. Nada aqui é segredo — são nomes de Página/@.
const conexaoContas = ref<ContaExterna[]>([])
const conexaoEscolha = ref('')

// Rótulo da plataforma da conta que está sendo conectada — vem da conta
// salva, não do select do formulário (que pode estar alterado sem salvar).
const conexaoPlataformaLabel = computed(() => {
  const p = conexao.value?.rede.plataforma as Plataforma | undefined
  return (p && PLATAFORMA_LABELS[p]) || p || ''
})

const tokenPillTitle = computed(() => {
  const rede = modal.value?.rede
  if (!rede?.has_token) return 'sem credencial — o robô não publica sozinho nesta conta'
  const partes = ['a credencial fica cifrada no servidor e não é exibida']
  if (rede.token_expires_at) {
    const d = new Date(rede.token_expires_at)
    if (!Number.isNaN(d.getTime())) partes.unshift(`vence em ${d.toLocaleDateString('pt-BR')}`)
  }
  return partes.join(' — ')
})

function abrirConexao() {
  if (!modal.value?.rede || !canEdit.value) return
  tokenValor.value = ''
  tokenVisible.value = false
  conexaoErr.value = null
  conexaoContas.value = []
  conexaoEscolha.value = ''
  conexao.value = { rede: modal.value.rede }
}

function fecharConexao() {
  // Zera o plaintext do token — inclusive quando o operador desiste no meio
  // da escolha da conta.
  tokenValor.value = ''
  tokenVisible.value = false
  conexaoErr.value = null
  conexaoContas.value = []
  conexaoEscolha.value = ''
  conexao.value = null
}

// Reflete o novo estado da credencial na conta aberta E na cópia do grid
// (o PATCH/POST não recarrega a grade inteira).
function aplicaConta(rede: RedeSocialOut, patch: Partial<RedeSocialOut>) {
  const alvos = new Set<RedeSocialOut>([rede])
  const doGrid = rowDaMarca(rede.marca_id)?.cells[rede.plataforma]?.find((x) => x.id === rede.id)
  if (doGrid) alvos.add(doGrid)
  const aberta = modal.value?.rede
  if (aberta && aberta.id === rede.id) alvos.add(aberta)
  for (const alvo of alvos) Object.assign(alvo, patch)
}

async function conectar() {
  const rede = conexao.value?.rede
  if (!rede || !canEdit.value || conectando.value) return
  const token = tokenValor.value.trim()
  if (!token) {
    conexaoErr.value = 'cole o token gerado no Business Suite'
    return
  }
  if (conexaoContas.value.length && !conexaoEscolha.value) {
    conexaoErr.value = 'escolha qual conta este token deve publicar'
    return
  }
  conectando.value = true
  conexaoErr.value = null
  try {
    const r = await api<ConexaoOut>(`/api/redes-sociais/${rede.id}/conectar`, {
      method: 'POST',
      // O token vai no CORPO (nunca na URL) e não fica em variável nossa.
      body: { access_token: token, external_user_id: conexaoEscolha.value || null },
    })
    if (!r.ok) {
      // Token válido, mas o backend não soube casar com o @ cadastrado: a
      // tela mostra o que ele enxerga e o operador escolhe — o campo do
      // token continua preenchido só porque o reenvio precisa dele.
      conexaoContas.value = r.contas || []
      conexaoEscolha.value = ''
      conexaoErr.value = conexaoContas.value.length
        ? 'escolha qual conta este token deve publicar e confirme'
        : 'este token não enxerga nenhuma Página/conta — gere outro com as permissões da conta'
      return
    }
    aplicaConta(rede, {
      has_token: true,
      token_status: 'ok',
      token_conta_externa: r.external_username || r.external_user_id,
      token_expires_at: r.token_expires_at,
    })
    fecharConexao()
  } catch (e: any) {
    conexaoErr.value = apiErrMsg(e, CONEXAO_ERROS)
  } finally {
    conectando.value = false
  }
}

async function desconectar() {
  const rede = modal.value?.rede
  if (!rede?.has_token || !canEdit.value) return
  const nome = rede.conta ? `@${rede.conta}` : 'esta conta'
  if (!confirm(`Desconectar a credencial de ${nome}?\n\nO robô para de publicar sozinho nesta conta na hora.`)) return
  saving.value = true
  modalErr.value = null
  try {
    await api(`/api/redes-sociais/${rede.id}/conectar`, { method: 'DELETE' })
    aplicaConta(rede, {
      has_token: false,
      token_status: null,
      token_conta_externa: null,
      token_expires_at: null,
    })
    fecharConexao()
  } catch (e: any) {
    modalErr.value = apiErrMsg(e, CONEXAO_ERROS)
  } finally {
    saving.value = false
  }
}

// Filtrar também descarta senha revelada (SPEC v2) — célula e modal.
watch(search, () => {
  clearRevealed()
  hideSenhaModal()
})

onUnmounted(() => {
  clearRevealed()
  hideSenhaModal()
  fecharConexao()
  form.value.senha = ''
  senhaMarcaValor.value = ''
  tokenValor.value = ''
})

// O grid não tem senha — pode carregar no SSR (padrão store-info.vue).
// Fica depois das declarações porque load() chama clearRevealed()/hideSenhaModal().
await load()
</script>

<template>
  <div class="space-y-4">
    <RouteTabs :tabs="TABS_CADASTROS" />
    <PageHeader
      title="Redes Sociais"
      description="Como na planilha: fone/WhatsApp, usuário e senha por marca; uma coluna por rede com os @ das contas. Clique na célula para editar; + cria uma conta."
    >
      <template #actions>
        <Button size="sm" variant="ghost" :disabled="loading" @click="load">
          <RefreshCw class="size-4 mr-1" :class="{ 'animate-spin': loading }" /> recarregar
        </Button>
        <Input v-model="search" placeholder="buscar marca, @conta, e-mail ou fone…" class="h-9 w-60" />
        <Button
          size="sm"
          :variant="soComContas ? 'ghost' : 'default'"
          :title="soComContas ? 'mostrar também as marcas sem nenhuma conta' : 'mostrar só as marcas que já têm conta'"
          @click="soComContas = !soComContas"
        >
          {{ soComContas ? 'Todas as marcas' : 'Só com contas' }}
        </Button>
        <Button size="sm" variant="outline" title="Meta Verified: o que ter em mãos e onde pedir" @click="showGuia = true">
          <BadgeCheck class="size-4 mr-1" /> Como verificar
        </Button>
        <Button v-if="canEdit" size="sm" :disabled="!grid?.rows.length" @click="openCreate()">
          <Plus class="size-4 mr-1" /> Nova conta
        </Button>
      </template>
    </PageHeader>

    <div v-if="error" class="rounded border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive flex items-center gap-2">
      <AlertCircle class="h-4 w-4" /> {{ error }}
    </div>

    <div class="border rounded-lg overflow-auto max-h-[calc(100vh-220px)]">
      <table class="w-full text-xs border-collapse">
        <thead class="sticky top-0 bg-muted z-10">
          <tr>
            <!-- primeira coluna fixa na rolagem horizontal (companies/index.vue) -->
            <th class="text-left px-2 py-2 font-medium border-b border-border min-w-[150px] sticky left-0 bg-muted z-20">Marca</th>
            <th class="text-left px-2 py-2 font-medium border-b border-border w-[130px] min-w-[130px]">Fone / WhatsApp</th>
            <th class="text-left px-2 py-2 font-medium border-b border-border min-w-[110px]">
              Usuário <span class="font-normal text-muted-foreground">(e-mail)</span>
            </th>
            <th class="text-left px-2 py-2 font-medium border-b border-border w-[90px] min-w-[90px]">Senha</th>
            <th
              v-for="p in PLATAFORMAS"
              :key="p"
              class="px-2 py-2 font-medium border-b border-border min-w-[96px] text-center"
            >
              {{ PLATAFORMA_LABELS[p] }}
            </th>
            <th
              v-for="col in MARCA_TEXT_COLS"
              :key="col.key"
              class="text-left px-2 py-2 font-medium border-b border-border"
              :class="col.th"
            >
              {{ col.label }}
            </th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading && !grid">
            <td :colspan="colspan" class="px-3 py-6 text-center text-muted-foreground">
              <Loader2 class="size-4 animate-spin inline-block mr-1 align-[-2px]" /> carregando…
            </td>
          </tr>
          <tr v-else-if="!grid?.rows.length">
            <td :colspan="colspan" class="px-3 py-6 text-center text-muted-foreground">
              nenhuma marca cadastrada — crie em
              <NuxtLink to="/marcas" class="underline hover:text-foreground">Marcas</NuxtLink>
            </td>
          </tr>
          <tr v-else-if="!filteredRows.length && !search">
            <td :colspan="colspan" class="px-3 py-6 text-center text-muted-foreground">
              nenhuma marca com conta ainda — clique em "Todas as marcas" e use o + pra criar
            </td>
          </tr>
          <tr v-else-if="!filteredRows.length">
            <td :colspan="colspan" class="px-3 py-6 text-center text-muted-foreground">
              nenhuma marca ou conta encontrada para "{{ search }}"
            </td>
          </tr>
          <tr v-for="row in filteredRows" :key="row.marca.id" class="border-t hover:bg-muted/20 align-top">
            <!-- marca (sticky) + miniatura do logo -->
            <td class="px-2 py-1.5 sticky left-0 bg-background">
              <div class="flex items-center gap-1.5 group">
                <img
                  v-if="row.marca.has_logo"
                  :src="logoSrc(row.marca)"
                  alt=""
                  class="h-6 w-auto max-w-[36px] object-contain rounded shrink-0"
                />
                <span
                  class="font-medium flex-1 truncate max-w-[160px]"
                  :class="{ 'opacity-50': !row.marca.ativo }"
                  :title="row.marca.ativo ? row.marca.nome : `${row.marca.nome} (inativa)`"
                >
                  {{ row.marca.nome }}
                </span>
                <NuxtLink
                  to="/marcas"
                  class="opacity-0 group-hover:opacity-100 shrink-0 p-0.5 hover:bg-muted rounded"
                  title="Abrir em Marcas"
                  @click.stop
                >
                  <ExternalLink class="size-3 text-muted-foreground" />
                </NuxtLink>
              </div>
            </td>

            <!-- fone/whatsapp: número inline; ao lado, pontinho do status do
                 Meta Verified do Zap (clique → select inline); abaixo, obs
                 da verificação (protocolo/data) quando há pedido -->
            <td class="px-2 py-1.5">
              <div class="flex items-center gap-1">
                <span
                  class="flex-1 min-w-0 truncate px-0.5"
                  :class="inlineClass(row.marca.id, 'sac_fone', !row.marca.sac_fone)"
                  :title="canEdit ? 'clique para editar' : undefined"
                  @click="startEdit(row, 'sac_fone')"
                >
                  <input
                    v-if="isEditing(row.marca.id, 'sac_fone')"
                    :ref="setEditInputRef"
                    v-model="editValue"
                    type="text"
                    inputmode="tel"
                    placeholder="só dígitos"
                    class="w-full text-xs bg-transparent outline-none"
                    @blur="commitEdit"
                    @keydown.enter.prevent="commitEdit"
                    @keydown.escape.prevent="cancelEdit"
                  />
                  <template v-else>{{ fmtFone(row.marca.sac_fone) || '—' }}</template>
                </span>
                <select
                  v-if="isEditing(row.marca.id, 'whatsapp_verificacao_status')"
                  :ref="setEditInputRef"
                  v-model="editValue"
                  class="text-[11px] border rounded bg-background max-w-[100px] shrink-0"
                  @click.stop
                  @change="commitEdit"
                  @blur="commitEdit"
                  @keydown.escape.prevent="cancelEdit"
                >
                  <option v-for="s in VERIFICACAO_STATUS" :key="s" :value="s">{{ VERIFICACAO_LABELS[s] }}</option>
                </select>
                <button
                  v-else
                  type="button"
                  class="inline-flex items-center gap-1 shrink-0 rounded px-0.5"
                  :class="{ 'hover:bg-muted': canEdit, 'cursor-default': !canEdit }"
                  :title="zapTitle(row.marca)"
                  @click.stop="startEdit(row, 'whatsapp_verificacao_status')"
                >
                  <BadgeCheck v-if="row.marca.whatsapp_verificacao_status === 'verificado'" class="size-3 text-primary" />
                  <span v-else class="size-2 rounded-full" :class="dotClass(row.marca.whatsapp_verificacao_status)" />
                  <span
                    v-if="VERIFICACAO_CURTO[row.marca.whatsapp_verificacao_status]"
                    class="text-[10px] text-muted-foreground"
                  >
                    {{ VERIFICACAO_CURTO[row.marca.whatsapp_verificacao_status] }}
                  </span>
                </button>
              </div>
              <div
                v-if="row.marca.whatsapp_verificacao_status !== 'nao_solicitado' || isEditing(row.marca.id, 'whatsapp_verificacao_obs')"
                class="mt-0.5 text-[10px] truncate max-w-[130px] px-0.5"
                :class="inlineClass(row.marca.id, 'whatsapp_verificacao_obs', !row.marca.whatsapp_verificacao_obs)"
                :title="row.marca.whatsapp_verificacao_obs || (canEdit ? 'obs da verificação (protocolo / data / etapa)' : undefined)"
                @click="startEdit(row, 'whatsapp_verificacao_obs')"
              >
                <input
                  v-if="isEditing(row.marca.id, 'whatsapp_verificacao_obs')"
                  :ref="setEditInputRef"
                  v-model="editValue"
                  type="text"
                  placeholder="protocolo / data"
                  class="w-full text-[10px] bg-transparent outline-none"
                  @blur="commitEdit"
                  @keydown.enter.prevent="commitEdit"
                  @keydown.escape.prevent="cancelEdit"
                />
                <template v-else>{{ row.marca.whatsapp_verificacao_obs || 'obs…' }}</template>
              </div>
            </td>

            <!-- usuário = e-mail do SAC (inline; truncate + title) -->
            <td class="px-2 py-1.5">
              <div
                class="max-w-[170px] truncate px-0.5"
                :class="inlineClass(row.marca.id, 'sac_email', !row.marca.sac_email)"
                :title="row.marca.sac_email || (canEdit ? 'clique para editar' : undefined)"
                @click="startEdit(row, 'sac_email')"
              >
                <input
                  v-if="isEditing(row.marca.id, 'sac_email')"
                  :ref="setEditInputRef"
                  v-model="editValue"
                  type="email"
                  autocomplete="off"
                  class="w-full text-xs bg-transparent outline-none"
                  @blur="commitEdit"
                  @keydown.enter.prevent="commitEdit"
                  @keydown.escape.prevent="cancelEdit"
                />
                <template v-else>{{ row.marca.sac_email || '—' }}</template>
              </div>
            </td>

            <!-- senha da marca: mascarada; olho/copiar/lápis no hover (edit) -->
            <td class="px-2 py-1.5">
              <div class="flex items-center gap-1 group">
                <span
                  class="flex-1 font-mono whitespace-nowrap"
                  :class="{ 'text-muted-foreground': !row.marca.has_sac_senha }"
                >
                  {{ row.marca.has_sac_senha ? (revealed.has(row.marca.id) ? (revealedSenhas.get(row.marca.id) || '••••') : '••••') : '—' }}
                </span>
                <button
                  v-if="row.marca.has_sac_senha && canEdit"
                  type="button"
                  class="opacity-0 group-hover:opacity-100 focus:opacity-100 transition-opacity p-0.5 hover:bg-muted rounded"
                  :title="revealed.has(row.marca.id) ? 'Ocultar' : 'Mostrar senha'"
                  @click="toggleReveal(row.marca.id)"
                >
                  <EyeOff v-if="revealed.has(row.marca.id)" class="h-3 w-3" />
                  <Eye v-else class="h-3 w-3" />
                </button>
                <button
                  v-if="row.marca.has_sac_senha && canEdit"
                  type="button"
                  class="opacity-0 group-hover:opacity-100 focus:opacity-100 transition-opacity p-0.5 hover:bg-muted rounded"
                  :title="copiedId === row.marca.id ? 'Copiado!' : 'Copiar senha'"
                  @click="copySacSenha(row.marca.id)"
                >
                  <Check v-if="copiedId === row.marca.id" class="h-3 w-3 text-emerald-600" />
                  <Copy v-else class="h-3 w-3" />
                </button>
                <button
                  v-if="canEdit"
                  type="button"
                  class="opacity-0 group-hover:opacity-100 focus:opacity-100 transition-opacity p-0.5 hover:bg-muted rounded"
                  :title="row.marca.has_sac_senha ? 'Trocar ou limpar a senha' : 'Definir a senha das redes/SAC'"
                  @click="openSenhaMarca(row.marca)"
                >
                  <Pencil class="h-3 w-3" />
                </button>
              </div>
            </td>

            <!-- uma célula por plataforma: chips das contas + "+" no hover -->
            <td v-for="p in PLATAFORMAS" :key="p" class="px-2 py-1.5 text-center group">
              <div class="flex flex-wrap items-center justify-center gap-1">
                <button
                  v-for="r in row.cells[p] || []"
                  :key="r.id"
                  type="button"
                  class="inline-flex items-center gap-1 max-w-full rounded-full border px-2 py-0.5 text-xs hover:bg-accent transition-colors"
                  :class="r.ativo ? '' : 'line-through text-muted-foreground'"
                  :title="chipTitle(r)"
                  @click="openEdit(r)"
                >
                  <span v-if="r.conta" class="truncate">@{{ r.conta }}</span>
                  <span v-else class="text-muted-foreground italic">sem conta</span>
                  <BadgeCheck v-if="r.verificacao_status === 'verificado'" class="size-3 text-primary shrink-0" />
                  <span
                    v-else-if="r.verificacao_status && r.verificacao_status !== 'nao_solicitado'"
                    class="size-1.5 rounded-full shrink-0"
                    :class="dotClass(r.verificacao_status)"
                  />
                  <!-- conta que o robô publica sozinho (token + postagem_auto):
                       um Send miúdo, só pra dar de ver na grade -->
                  <span
                    v-if="postagemAutoOn(r)"
                    class="shrink-0 inline-flex"
                    title="postagem automática ligada"
                  >
                    <Send class="size-2.5 text-emerald-600" />
                  </span>
                </button>
                <button
                  v-if="canEdit"
                  type="button"
                  class="opacity-0 group-hover:opacity-100 focus:opacity-100 transition-opacity p-0.5 rounded hover:bg-muted text-muted-foreground"
                  :title="`nova conta em ${PLATAFORMA_LABELS[p]}`"
                  @click="openCreate(row.marca.id, p)"
                >
                  <Plus class="size-3" />
                </button>
              </div>
            </td>

            <!-- função / tipo / obs (da marca; inline) -->
            <td v-for="col in MARCA_TEXT_COLS" :key="col.key" class="px-2 py-1.5">
              <div
                class="px-0.5"
                :class="[col.truncate ? `${col.truncate} truncate` : '', inlineClass(row.marca.id, col.key, !row.marca[col.key])]"
                :title="col.truncate ? (row.marca[col.key] || (canEdit ? 'clique para editar' : undefined)) : (canEdit ? 'clique para editar' : undefined)"
                @click="startEdit(row, col.key)"
              >
                <input
                  v-if="isEditing(row.marca.id, col.key)"
                  :ref="setEditInputRef"
                  v-model="editValue"
                  type="text"
                  class="w-full text-xs bg-transparent outline-none"
                  @blur="commitEdit"
                  @keydown.enter.prevent="commitEdit"
                  @keydown.escape.prevent="cancelEdit"
                />
                <template v-else>{{ row.marca[col.key] || '—' }}</template>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- modal criar/editar conta (markup de cadastros/index.vue) -->
    <div v-if="modal" class="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4" @click.self="closeModal">
      <div class="bg-background border rounded-lg w-full max-w-2xl p-5 space-y-4 max-h-[90vh] overflow-auto">
        <div class="flex items-center gap-2">
          <h2 class="text-lg font-semibold">{{ modal.rede ? (canEdit ? 'Editar conta' : 'Conta') : 'Nova conta' }}</h2>
          <span v-if="modal.rede" class="text-xs text-muted-foreground truncate">
            {{ modal.rede.marca_nome }} · {{ plataformaLabel }}
          </span>
          <Button class="ml-auto" size="sm" variant="ghost" @click="closeModal">
            <X class="size-4" />
          </Button>
        </div>

        <div class="grid grid-cols-2 gap-3">
          <div>
            <Label>Marca *</Label>
            <select v-model="form.marca_id" :disabled="!canEdit" class="w-full h-10 border rounded-md px-2 text-sm bg-background">
              <option value="" disabled>— escolha —</option>
              <option v-for="m in marcasOpcoes" :key="m.id" :value="m.id">{{ m.label }}</option>
            </select>
          </div>
          <div>
            <Label>Plataforma *</Label>
            <select v-model="form.plataforma" :disabled="!canEdit" class="w-full h-10 border rounded-md px-2 text-sm bg-background">
              <option v-for="p in PLATAFORMAS" :key="p" :value="p">{{ PLATAFORMA_LABELS[p] }}</option>
            </select>
          </div>
          <div>
            <Label>Conta</Label>
            <Input v-model="form.conta" :disabled="!canEdit" placeholder="sem @" autocomplete="off" />
            <p class="text-[11px] text-muted-foreground mt-0.5">sem @ — em branco fica uma linha "sem conta"</p>
          </div>
          <div>
            <Label>Usuário</Label>
            <Input v-model="form.usuario" :disabled="!canEdit" placeholder="login, se diferente da conta" autocomplete="off" />
          </div>
          <div class="col-span-2">
            <Label>Link do perfil</Label>
            <div class="relative">
              <Input v-model="form.url" :disabled="!canEdit" class="pr-9" :placeholder="urlSugerida || 'https://…'" autocomplete="off" />
              <a
                v-if="urlAbrir"
                :href="urlAbrir"
                target="_blank"
                rel="noopener"
                class="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                :title="form.url.trim() ? 'abrir link' : 'abrir link sugerido'"
              >
                <ExternalLink class="size-4" />
              </a>
            </div>
          </div>
          <!-- e-mail/fone PRÓPRIOS da conta — vazio herda os da marca (placeholder) -->
          <div>
            <Label>E-mail</Label>
            <Input v-model="form.email" :disabled="!canEdit" type="email" :placeholder="emailPlaceholder" autocomplete="off" />
            <p class="text-[11px] text-muted-foreground mt-0.5">em branco usa o da marca</p>
          </div>
          <div>
            <Label>Fone</Label>
            <Input v-model="form.fone" :disabled="!canEdit" :placeholder="fonePlaceholder" autocomplete="off" />
            <p class="text-[11px] text-muted-foreground mt-0.5">em branco usa o da marca</p>
          </div>
          <div class="col-span-2">
            <Label>Senha</Label>
            <div class="relative">
              <Input
                v-model="form.senha"
                type="text"
                :disabled="!canEdit"
                autocomplete="off"
                data-1p-ignore
                data-lpignore="true"
                data-form-type="other"
                name="rede-social-secret"
                class="pr-16"
                :style="senhaVisible ? undefined : { WebkitTextSecurity: 'disc' }"
                :placeholder="senhaPlaceholder"
              />
              <div v-if="canEdit" class="absolute right-2 top-1/2 -translate-y-1/2 flex items-center gap-1">
                <button
                  type="button"
                  class="text-muted-foreground hover:text-foreground disabled:opacity-50"
                  :disabled="senhaRevealing"
                  :title="senhaVisible ? 'ocultar' : (modal.rede?.has_senha_efetiva && !form.senha ? 'ver a senha em uso' : 'mostrar')"
                  @click="toggleSenha"
                >
                  <Loader2 v-if="senhaRevealing" class="size-4 animate-spin" />
                  <EyeOff v-else-if="senhaVisible" class="size-4" />
                  <Eye v-else class="size-4" />
                </button>
                <button
                  v-if="form.senha || modal.rede?.has_senha_efetiva"
                  type="button"
                  class="text-muted-foreground hover:text-foreground"
                  :title="senhaCopiada ? 'Copiado!' : 'copiar senha'"
                  @click="copySenha"
                >
                  <Check v-if="senhaCopiada" class="size-4 text-emerald-600" />
                  <Copy v-else class="size-4" />
                </button>
              </div>
            </div>
            <div class="flex items-center justify-between mt-0.5 gap-2">
              <p class="text-[11px] text-muted-foreground">{{ senhaHint }}</p>
              <button
                v-if="modal.rede?.senha_origem === 'conta' && canEdit"
                type="button"
                class="text-[11px] text-destructive hover:underline disabled:opacity-50 whitespace-nowrap"
                :disabled="saving"
                @click="voltarHerdarSenha"
              >
                voltar a herdar a da marca
              </button>
            </div>
          </div>
          <!-- Publicação automática (Eduardo, 15/09/2026): credencial da
               conta + interruptor do robô + tetos desta conta. Só na edição:
               conectar precisa da conta já salva e RedeSocialCreate não tem
               esses campos (a conta nasce sem token e sem postagem_auto). -->
          <div v-if="modal.rede" class="col-span-2 border rounded-md p-3 space-y-2">
            <div class="flex items-center gap-2 flex-wrap">
              <Send class="size-4 text-muted-foreground shrink-0" />
              <h3 class="text-sm font-semibold">Publicação automática</h3>
              <span
                class="text-[11px] rounded-full px-2 py-0.5 whitespace-nowrap"
                :class="tokenPillClass(modal.rede)"
                :title="tokenPillTitle"
              >
                {{ tokenPillTexto(modal.rede) }}
              </span>
              <div v-if="canEdit" class="ml-auto flex items-center gap-2">
                <template v-if="modal.rede.has_token">
                  <button
                    type="button"
                    class="text-[11px] text-muted-foreground hover:text-foreground hover:underline disabled:opacity-50"
                    :disabled="saving"
                    title="colar outro token no lugar deste (para renovar um vencido, por exemplo)"
                    @click="abrirConexao"
                  >
                    trocar token
                  </button>
                  <Button size="sm" variant="ghost" class="text-destructive hover:text-destructive" :disabled="saving" @click="desconectar">
                    <Unlink class="size-4 mr-1" /> desconectar
                  </Button>
                </template>
                <Button v-else size="sm" variant="outline" :disabled="saving" @click="abrirConexao">
                  <Plug class="size-4 mr-1" /> conectar
                </Button>
              </div>
            </div>

            <label class="flex items-start gap-2 text-sm">
              <input v-model="form.postagem_auto" type="checkbox" :disabled="!canEdit" class="mt-1" />
              <span>
                postar automaticamente nesta conta
                <span class="block text-[11px] text-muted-foreground">
                  o robô só publica sozinho em conta com isto ligado; publicar manualmente na aba Criativos funciona de qualquer jeito
                </span>
              </span>
            </label>
            <p v-if="form.postagem_auto && !modal.rede.has_token" class="text-[11px] text-amber-600 dark:text-amber-400">
              ligado, mas sem credencial — conecte o token para o robô conseguir publicar.
            </p>

            <div class="grid grid-cols-2 gap-3">
              <div>
                <Label>posts por dia</Label>
                <Input
                  v-model="form.postagem_max_dia"
                  type="number"
                  min="1"
                  step="1"
                  :disabled="!canEdit"
                  placeholder="padrão: 2"
                  autocomplete="off"
                />
              </div>
              <div>
                <Label>intervalo mínimo (min)</Label>
                <Input
                  v-model="form.postagem_intervalo_min"
                  type="number"
                  min="1"
                  step="1"
                  :disabled="!canEdit"
                  placeholder="padrão: 90"
                  autocomplete="off"
                />
              </div>
            </div>
            <p class="text-[11px] text-muted-foreground">
              em branco usa o padrão do servidor (2 posts/dia, 90 min entre um post e outro nesta conta)
            </p>
          </div>
          <p v-else class="col-span-2 text-[11px] text-muted-foreground">
            publicação automática (token e limites): configure depois de salvar a conta.
          </p>

          <!-- verificação (Meta Verified): o DaVinci só registra o andamento -->
          <div>
            <Label>Verificação</Label>
            <select v-model="form.verificacao_status" :disabled="!canEdit" class="w-full h-10 border rounded-md px-2 text-sm bg-background">
              <option v-for="s in VERIFICACAO_STATUS" :key="s" :value="s">{{ VERIFICACAO_LABELS[s] }}</option>
            </select>
          </div>
          <div>
            <Label>Obs da verificação</Label>
            <Input v-model="form.verificacao_obs" :disabled="!canEdit" placeholder="protocolo / data / etapa" autocomplete="off" />
          </div>
          <div class="col-span-2">
            <Label>Obs</Label>
            <textarea v-model="form.obs" :disabled="!canEdit" rows="2" class="w-full rounded-md border bg-background px-2 py-1.5 text-sm resize-y" />
          </div>
          <label v-if="modal.rede" class="flex items-center gap-2 text-sm">
            <input v-model="form.ativo" type="checkbox" :disabled="!canEdit" /> ativa
          </label>
        </div>

        <div v-if="modalErr" class="text-sm text-red-500">erro: {{ modalErr }}</div>
        <div class="flex items-center gap-2">
          <Button
            v-if="modal.rede && canDelete"
            variant="ghost"
            class="text-destructive hover:text-destructive"
            :disabled="saving"
            @click="removeRede"
          >
            <Trash2 class="size-4 mr-1" /> Excluir
          </Button>
          <div class="ml-auto flex gap-2">
            <Button variant="ghost" :disabled="saving" @click="closeModal">{{ canEdit ? 'cancelar' : 'fechar' }}</Button>
            <Button v-if="canEdit" :disabled="saving || !form.marca_id" @click="saveRede">
              {{ saving ? 'salvando…' : 'Salvar' }}
            </Button>
          </div>
        </div>
      </div>
    </div>

    <!-- sub-modal: conectar a credencial de publicação (POST /{id}/conectar).
         Fica ACIMA do modal da conta (z-[60]) porque é um passo dentro dele.
         O token é digitado mascarado (padrão nf-cadastros.vue) e nunca é
         relido do servidor: some daqui assim que a conexão dá certo. -->
    <div v-if="conexao" class="fixed inset-0 bg-black/60 flex items-center justify-center z-[60] p-4" @click.self="fecharConexao">
      <div class="bg-background border rounded-lg w-full max-w-lg p-5 space-y-3 max-h-[90vh] overflow-auto">
        <div class="flex items-center gap-2">
          <Plug class="size-4 text-muted-foreground shrink-0" />
          <h2 class="text-base font-semibold">Conectar publicação</h2>
          <span class="text-xs text-muted-foreground truncate">
            {{ conexao.rede.conta ? `@${conexao.rede.conta}` : 'conta sem @' }} · {{ conexaoPlataformaLabel }}
          </span>
          <Button class="ml-auto" size="sm" variant="ghost" :disabled="conectando" @click="fecharConexao">
            <X class="size-4" />
          </Button>
        </div>

        <p class="text-[11px] text-muted-foreground">
          O token vem do <strong>Business Suite</strong> → Configurações → Usuários → <strong>Usuários do sistema</strong>
          (System users) → <strong>Gerar novo token</strong> (Generate new token), escolhendo o app e as permissões da
          Página/Instagram desta conta.
        </p>

        <div class="relative">
          <Input
            v-model="tokenValor"
            type="text"
            autocomplete="off"
            data-1p-ignore
            data-lpignore="true"
            data-form-type="other"
            name="rede-social-token"
            spellcheck="false"
            class="pr-9 font-mono"
            :style="tokenVisible ? undefined : { WebkitTextSecurity: 'disc' }"
            placeholder="cole aqui o token"
            @keydown.enter.prevent="conectar"
          />
          <button
            type="button"
            class="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
            :title="tokenVisible ? 'ocultar' : 'mostrar o que foi colado'"
            @click="tokenVisible = !tokenVisible"
          >
            <EyeOff v-if="tokenVisible" class="size-4" />
            <Eye v-else class="size-4" />
          </button>
        </div>
        <p class="text-[11px] text-muted-foreground">
          O token não sai mais daqui: fica cifrado no servidor e não volta em tela, log ou link — para trocar, cole um novo.
        </p>

        <!-- ok=false: o backend não soube casar o token com o @ cadastrado e
             devolveu o que ele enxerga — o operador escolhe e reenvia. -->
        <div v-if="conexaoContas.length" class="border rounded-md p-2 space-y-1">
          <div class="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
            este token enxerga — escolha a conta
          </div>
          <label
            v-for="(c, i) in conexaoContas"
            :key="contaExternaId(c, conexao.rede.plataforma) || i"
            class="flex items-center gap-2 text-sm"
            :class="{ 'opacity-50': !contaExternaId(c, conexao.rede.plataforma) }"
          >
            <input
              v-model="conexaoEscolha"
              type="radio"
              name="conexao-conta"
              :value="contaExternaId(c, conexao.rede.plataforma)"
              :disabled="!contaExternaId(c, conexao.rede.plataforma)"
            />
            <span class="truncate">{{ contaExternaLabel(c) }}</span>
          </label>
        </div>

        <div v-if="conexaoErr" class="text-sm text-red-500">erro: {{ conexaoErr }}</div>

        <div class="flex justify-end gap-2">
          <Button variant="ghost" size="sm" :disabled="conectando" @click="fecharConexao">cancelar</Button>
          <Button size="sm" :disabled="conectando || !tokenValor" @click="conectar">
            {{ conectando ? 'conectando…' : 'Conectar' }}
          </Button>
        </div>
      </div>
    </div>

    <!-- mini-modal: senha das redes/SAC da marca (PATCH {sac_senha}) -->
    <div v-if="senhaMarca" class="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4" @click.self="closeSenhaMarca">
      <div class="bg-background border rounded-lg w-full max-w-sm p-5 space-y-3">
        <div class="flex items-center gap-2">
          <h2 class="text-base font-semibold">Senha das redes/SAC</h2>
          <span class="text-xs text-muted-foreground truncate">{{ senhaMarca.marca.nome }}</span>
          <Button class="ml-auto" size="sm" variant="ghost" @click="closeSenhaMarca">
            <X class="size-4" />
          </Button>
        </div>
        <p class="text-[11px] text-muted-foreground">
          Compartilhada pelas contas da marca que não têm senha própria (como na planilha).
        </p>
        <div class="relative">
          <Input
            v-model="senhaMarcaValor"
            type="text"
            autocomplete="off"
            data-1p-ignore
            data-lpignore="true"
            data-form-type="other"
            name="marca-sac-secret"
            class="pr-9"
            :style="senhaMarcaVisible ? undefined : { WebkitTextSecurity: 'disc' }"
            :placeholder="senhaMarca.marca.has_sac_senha ? 'branco = manter a salva' : 'nova senha'"
            @keydown.enter.prevent="saveSenhaMarca"
          />
          <button
            type="button"
            class="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground disabled:opacity-50"
            :disabled="senhaMarcaRevealing"
            :title="senhaMarcaVisible ? 'ocultar' : (senhaMarca.marca.has_sac_senha && !senhaMarcaValor ? 'ver senha salva' : 'mostrar')"
            @click="toggleSenhaMarca"
          >
            <Loader2 v-if="senhaMarcaRevealing" class="size-4 animate-spin" />
            <EyeOff v-else-if="senhaMarcaVisible" class="size-4" />
            <Eye v-else class="size-4" />
          </button>
        </div>
        <div v-if="senhaMarcaErr" class="text-sm text-red-500">erro: {{ senhaMarcaErr }}</div>
        <div class="flex items-center gap-2">
          <button
            v-if="senhaMarca.marca.has_sac_senha"
            type="button"
            class="text-[11px] text-destructive hover:underline disabled:opacity-50"
            :disabled="senhaMarcaSaving"
            @click="limparSenhaMarca"
          >
            limpar senha
          </button>
          <div class="ml-auto flex gap-2">
            <Button variant="ghost" size="sm" :disabled="senhaMarcaSaving" @click="closeSenhaMarca">cancelar</Button>
            <Button size="sm" :disabled="senhaMarcaSaving || !senhaMarcaValor" @click="saveSenhaMarca">
              {{ senhaMarcaSaving ? 'salvando…' : 'Salvar' }}
            </Button>
          </div>
        </div>
      </div>
    </div>

    <!-- "Como verificar": guia do Meta Verified (lib/redesSociais.ts) -->
    <div v-if="showGuia" class="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4" @click.self="showGuia = false">
      <div class="bg-background border rounded-lg w-full max-w-3xl p-5 space-y-4 max-h-[90vh] overflow-auto">
        <div class="flex items-center gap-2">
          <BadgeCheck class="size-5 text-primary" />
          <h2 class="text-lg font-semibold">Como verificar (Meta Verified)</h2>
          <Button class="ml-auto" size="sm" variant="ghost" @click="showGuia = false">
            <X class="size-4" />
          </Button>
        </div>
        <p class="text-sm text-muted-foreground">
          O pedido é feito pela equipe no app da plataforma, no celular que tem a conta — o DaVinci só
          <strong>registra o andamento</strong>: o status e a observação (protocolo/data) de cada conta e do WhatsApp da marca.
        </p>
        <section v-for="g in VERIFICACAO_GUIA" :key="g.titulo" class="border rounded-md p-3 space-y-2">
          <h3 class="font-semibold">{{ g.titulo }}</h3>
          <p class="text-sm">{{ g.resumo }}</p>
          <div>
            <div class="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">O que ter em mãos</div>
            <ul class="list-disc pl-5 text-sm space-y-0.5">
              <li v-for="(req, i) in g.requisitos" :key="i">{{ req }}</li>
            </ul>
          </div>
          <div>
            <div class="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Passo a passo</div>
            <ol class="list-decimal pl-5 text-sm space-y-0.5">
              <li v-for="(passo, i) in g.passos" :key="i">{{ passo }}</li>
            </ol>
          </div>
          <div class="flex flex-wrap gap-x-4 gap-y-1">
            <a
              v-for="l in g.links"
              :key="l.url"
              :href="l.url"
              target="_blank"
              rel="noopener"
              class="inline-flex items-center gap-1 text-xs text-primary underline underline-offset-2"
            >
              <ExternalLink class="size-3" /> {{ l.label }}
            </a>
          </div>
        </section>
        <div class="flex justify-end">
          <Button variant="ghost" @click="showGuia = false">fechar</Button>
        </div>
      </div>
    </div>
  </div>
</template>
