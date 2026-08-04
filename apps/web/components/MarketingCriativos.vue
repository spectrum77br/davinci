<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import {
  Check, X, Trash2, Upload, Loader2, Plus, Download, CloudUpload, Search,
  Film, Image as ImageIcon, File as FileIcon,
} from 'lucide-vue-next'

type CreativeFile = {
  id: string
  file_name: string
  file_mime: string | null
  file_size: number | null
}

type Creative = {
  id: string
  modelo: string
  marca: string | null
  sku: string | null
  roteiro: string | null
  files: CreativeFile[]
  aprovado: boolean | null
  pushed_at: string | null
  pushed_dest: string | null
  created_at: string | null
}

type Field = 'modelo' | 'marca' | 'sku' | 'roteiro'

const { api } = useApi()
const toasts = useToasts()
const auth = useAuthStore()
const isAdmin = computed(() => auth.user?.role === 'admin')
const canEdit = useCan('marketing_criativos', 'edit')

// Opções fixas — modelo e marca são escolhidos, não digitados.
const MODELO_OPTIONS = ['imagem', 'video 15s', 'video 30s', 'video 60s']
const MARCA_OPTIONS = ['uranyx', 'charlots']

const rows = ref<Creative[]>([])
const loading = ref(false)
const uploadingId = ref<string | null>(null)
const approvingId = ref<string | null>(null)

const ERR_PT: Record<string, string> = {
  ja_enviado_pro_mega: 'Essa linha já foi pro MEGA — não dá mais pra mexer nos arquivos.',
  sem_arquivo: 'Falta anexar pelo menos um arquivo antes de aprovar.',
  sem_sku: 'Preencha o SKU antes de aprovar.',
  produto_nao_encontrado: 'Nenhum produto na tabela de preços tem esse SKU.',
  produto_sem_pasta: 'O produto desse SKU ainda não tem pasta no MEGA.',
  modelo_obrigatorio: 'O campo modelo é obrigatório.',
  arquivo_sumiu: 'O arquivo não foi encontrado no servidor — anexe de novo.',
  muitos_arquivos: 'Limite de 20 arquivos por linha.',
  nome_invalido: 'Nome de arquivo inválido.',
  forbidden: 'Você não tem permissão pra isso.',
}

function errMsg(e: any): string {
  const code = e?.data?.detail?.code
  if (code && ERR_PT[code]) return ERR_PT[code]
  if (code === 'mega_error') {
    return `Erro no MEGA: ${e?.data?.detail?.message ?? 'falha no envio'}`
  }
  return e?.data?.detail?.message ?? e?.message ?? 'Erro inesperado'
}

async function load() {
  loading.value = true
  try {
    rows.value = await api<Creative[]>('/api/marketing/creatives')
  } catch (e: any) {
    toasts.error('Erro ao carregar criativos', errMsg(e))
  } finally {
    loading.value = false
  }
}
onMounted(() => { void load() })

// ---- filtro ---------------------------------------------------------------
const q = ref('')
const statusFilter = ref<'todos' | 'pendente' | 'aprovado' | 'reprovado'>('todos')

const filteredRows = computed(() => {
  const term = q.value.trim().toLowerCase()
  return rows.value.filter((r) => {
    if (statusFilter.value === 'pendente' && r.aprovado !== null) return false
    if (statusFilter.value === 'aprovado' && r.aprovado !== true) return false
    if (statusFilter.value === 'reprovado' && r.aprovado !== false) return false
    if (!term) return true
    const hay = [r.modelo, r.marca, r.sku, r.roteiro, ...r.files.map((f) => f.file_name)]
      .filter(Boolean)
      .join(' ')
      .toLowerCase()
    return hay.includes(term)
  })
})

// ---- edição estilo planilha (clica na célula → edita; Enter/blur salva,
// ---- Esc cancela; flash verde ao salvar — mesmo padrão da Tabela de Preços)
const editing = ref<{ id: string; field: Field } | null>(null)
const editValue = ref('')
const flashed = ref<{ id: string; field: Field } | null>(null)

function isEditing(id: string, field: Field) {
  return editing.value?.id === id && editing.value?.field === field
}
function isFlashed(id: string, field: Field) {
  return flashed.value?.id === id && flashed.value?.field === field
}

function setEditInputRef(el: any) {
  if (el && typeof el.focus === 'function') {
    el.focus()
    if (el.tagName === 'INPUT' && typeof el.select === 'function') el.select()
  }
}

function startEdit(r: Creative, field: Field) {
  if (!canEdit.value) return
  editing.value = { id: r.id, field }
  editValue.value = (r[field] ?? '') as string
  void nextTick()
}

function cancelEdit() {
  editing.value = null
}

async function commitEdit() {
  const cur = editing.value
  if (!cur) return
  const r = rows.value.find((x) => x.id === cur.id)
  editing.value = null
  if (!r) return
  const before = (r[cur.field] ?? '') as string
  if (editValue.value === before) return
  try {
    const updated = await api<Creative>(`/api/marketing/creatives/${r.id}`, {
      method: 'PATCH',
      body: { [cur.field]: editValue.value },
    })
    Object.assign(r, updated)
    flashed.value = { id: r.id, field: cur.field }
    setTimeout(() => {
      if (flashed.value?.id === r.id && flashed.value?.field === cur.field) {
        flashed.value = null
      }
    }, 1200)
  } catch (e: any) {
    toasts.error('Erro ao salvar', errMsg(e))
  }
}

// ---- adicionar linha ------------------------------------------------------
const newRow = reactive({ modelo: '', marca: '', sku: '' })
const adding = ref(false)

async function addRow() {
  if (!newRow.modelo.trim()) {
    toasts.warning('Escolha o modelo (imagem, video 15s, video 30s ou video 60s)')
    return
  }
  adding.value = true
  try {
    const created = await api<Creative>('/api/marketing/creatives', {
      method: 'POST',
      body: { modelo: newRow.modelo, marca: newRow.marca || null, sku: newRow.sku || null },
    })
    rows.value = [...rows.value, created]
    newRow.modelo = ''
    newRow.marca = ''
    newRow.sku = ''
  } catch (e: any) {
    toasts.error('Erro ao adicionar', errMsg(e))
  } finally {
    adding.value = false
  }
}

// ---- arquivos (vários por linha) ------------------------------------------
const fileInput = ref<HTMLInputElement | null>(null)
const fileTarget = ref<Creative | null>(null)

function pickFile(r: Creative) {
  if (r.pushed_at) {
    toasts.warning(ERR_PT.ja_enviado_pro_mega)
    return
  }
  fileTarget.value = r
  fileInput.value?.click()
}

async function onFilePicked(ev: Event) {
  const input = ev.target as HTMLInputElement
  const files = Array.from(input.files ?? [])
  const target = fileTarget.value
  input.value = ''
  if (!files.length || !target) return
  uploadingId.value = target.id
  try {
    const fd = new FormData()
    for (const f of files) fd.append('files', f)
    const updated = await api<Creative>(
      `/api/marketing/creatives/${target.id}/arquivo`,
      { method: 'POST', body: fd },
    )
    Object.assign(target, updated)
    toasts.success(
      files.length === 1 ? 'Arquivo anexado' : `${files.length} arquivos anexados`,
      files.map((f) => f.name).join(', '),
    )
  } catch (e: any) {
    toasts.error('Erro no upload', errMsg(e))
  } finally {
    uploadingId.value = null
  }
}

async function removeFile(r: Creative, f: CreativeFile) {
  if (!window.confirm(`Apagar o arquivo "${f.file_name}"?`)) return
  try {
    const updated = await api<Creative>(
      `/api/marketing/creatives/${r.id}/arquivo/${f.id}`,
      { method: 'DELETE' },
    )
    Object.assign(r, updated)
    toasts.info('Arquivo apagado', f.file_name)
  } catch (e: any) {
    toasts.error('Erro ao apagar arquivo', errMsg(e))
  }
}

function fmtSize(n: number | null): string {
  if (!n) return ''
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`
  return `${(n / (1024 * 1024)).toFixed(1)} MB`
}

// ---- preview (imagem/vídeo no modal, com botão de baixar) ------------------
const preview = ref<{ row: Creative; file: CreativeFile } | null>(null)

function fileUrl(r: Creative, f: CreativeFile, download = false) {
  return `/api/marketing/creatives/${r.id}/arquivo/${f.id}${download ? '?download=1' : ''}`
}

const previewIsImage = computed(() => preview.value?.file.file_mime?.startsWith('image/') ?? false)
const previewIsVideo = computed(() => preview.value?.file.file_mime?.startsWith('video/') ?? false)

function openPreview(r: Creative, f: CreativeFile) {
  preview.value = { row: r, file: f }
}
function closePreview() {
  preview.value = null
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape' && preview.value) closePreview()
}
onMounted(() => window.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown))

// ---- aprovação (admin) ----------------------------------------------------
async function aprovar(r: Creative, ok: boolean) {
  approvingId.value = r.id
  const t = ok && !r.pushed_at
    ? toasts.push({ kind: 'info', title: 'Aprovando…', lines: 'Enviando os arquivos pra pasta do produto no MEGA.' }, 0)
    : null
  try {
    const updated = await api<Creative & { enviados?: number; fotos_count?: number | null }>(
      `/api/marketing/creatives/${r.id}/aprovar`,
      { method: 'POST', body: { aprovado: ok } },
    )
    Object.assign(r, updated)
    if (ok && updated.pushed_dest) {
      const n = updated.enviados ?? r.files.length
      toasts.success(
        n === 1 ? 'Aprovado — arquivo no MEGA' : `Aprovado — ${n} arquivos no MEGA`,
        `Pasta: ${updated.pushed_dest}`,
      )
    } else if (!ok) {
      toasts.info('Marcado como não aprovado')
    }
  } catch (e: any) {
    toasts.error('Erro na aprovação', errMsg(e))
  } finally {
    if (t !== null) toasts.dismiss(t)
    approvingId.value = null
  }
}

// ---- excluir --------------------------------------------------------------
async function remove(r: Creative) {
  if (!window.confirm(`Excluir a linha "${r.modelo}"?`)) return
  try {
    await api(`/api/marketing/creatives/${r.id}`, { method: 'DELETE' })
    rows.value = rows.value.filter((x) => x.id !== r.id)
  } catch (e: any) {
    toasts.error('Erro ao excluir', errMsg(e))
  }
}
</script>

<template>
  <div class="space-y-3">
    <input
      ref="fileInput"
      type="file"
      accept="image/*,video/*"
      multiple
      class="hidden"
      @change="onFilePicked"
    >

    <!-- filtro -->
    <div class="flex flex-wrap items-center gap-2">
      <div class="relative">
        <Search class="absolute left-2 top-1/2 -translate-y-1/2 size-3.5 text-muted-foreground" />
        <input
          v-model="q"
          type="text"
          placeholder="filtrar por modelo, marca, SKU, roteiro…"
          class="h-8 w-72 max-w-full rounded-md border bg-background pl-7 pr-2 text-xs outline-none focus:ring-2 focus:ring-ring"
        />
      </div>
      <select
        v-model="statusFilter"
        class="h-8 rounded-md border bg-background px-2 text-xs outline-none focus:ring-2 focus:ring-ring"
      >
        <option value="todos">todos</option>
        <option value="pendente">pendentes</option>
        <option value="aprovado">aprovados</option>
        <option value="reprovado">não aprovados</option>
      </select>
      <span class="ml-auto text-xs text-muted-foreground">
        {{ filteredRows.length }} de {{ rows.length }}
      </span>
    </div>

    <div class="border rounded-lg overflow-auto max-h-[calc(100vh-300px)]">
      <table class="w-full text-sm border-collapse">
        <thead class="sticky top-0 bg-muted z-10">
          <tr>
            <th class="text-left px-2 py-2 font-medium border-b border-border min-w-[120px]">Modelo</th>
            <th class="text-left px-2 py-2 font-medium border-b border-border min-w-[95px]">Marca</th>
            <th class="text-left px-2 py-2 font-medium border-b border-border w-28 min-w-[80px]">SKU</th>
            <th class="text-left px-2 py-2 font-medium border-b border-border min-w-[340px]">Roteiro</th>
            <th class="text-left px-2 py-2 font-medium border-b border-border min-w-[200px]">Arquivos</th>
            <th class="text-center px-2 py-2 font-medium border-b border-border w-24">Aprovado</th>
            <th class="text-center px-2 py-2 font-medium border-b border-border w-12"></th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading && !rows.length">
            <td colspan="7" class="text-center py-6 text-muted-foreground">
              <Loader2 class="inline h-4 w-4 animate-spin" /> carregando…
            </td>
          </tr>
          <tr v-else-if="!rows.length">
            <td colspan="7" class="text-center py-6 text-muted-foreground">
              Nenhum criativo ainda — adicione a primeira linha abaixo.
            </td>
          </tr>
          <tr v-else-if="!filteredRows.length">
            <td colspan="7" class="text-center py-6 text-muted-foreground">
              Nenhuma linha bate com o filtro.
            </td>
          </tr>

          <!-- data rows -->
          <tr v-for="r in filteredRows" :key="r.id" class="hover:bg-accent/30 align-top">
            <!-- modelo -->
            <td
              class="border border-border px-2 py-1.5 text-xs"
              :class="{
                'cursor-pointer': canEdit,
                'ring-2 ring-blue-500 ring-inset bg-background': isEditing(r.id, 'modelo'),
                'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(r.id, 'modelo'),
              }"
              @click="!isEditing(r.id, 'modelo') && startEdit(r, 'modelo')"
            >
              <select
                v-if="isEditing(r.id, 'modelo')"
                :ref="setEditInputRef"
                v-model="editValue"
                class="w-full text-xs bg-background outline-none rounded"
                @change="commitEdit" @blur="commitEdit" @keydown.escape.prevent="cancelEdit"
              >
                <option
                  v-if="editValue && !MODELO_OPTIONS.includes(editValue)"
                  :value="editValue"
                >{{ editValue }}</option>
                <option v-for="o in MODELO_OPTIONS" :key="o" :value="o">{{ o }}</option>
              </select>
              <span v-else class="font-medium">{{ r.modelo }}</span>
            </td>
            <!-- marca -->
            <td
              class="border border-border px-2 py-1.5 text-xs"
              :class="{
                'cursor-pointer': canEdit,
                'ring-2 ring-blue-500 ring-inset bg-background': isEditing(r.id, 'marca'),
                'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(r.id, 'marca'),
              }"
              @click="!isEditing(r.id, 'marca') && startEdit(r, 'marca')"
            >
              <select
                v-if="isEditing(r.id, 'marca')"
                :ref="setEditInputRef"
                v-model="editValue"
                class="w-full text-xs bg-background outline-none rounded"
                @change="commitEdit" @blur="commitEdit" @keydown.escape.prevent="cancelEdit"
              >
                <option value="">—</option>
                <option
                  v-if="editValue && !MARCA_OPTIONS.includes(editValue)"
                  :value="editValue"
                >{{ editValue }}</option>
                <option v-for="o in MARCA_OPTIONS" :key="o" :value="o">{{ o }}</option>
              </select>
              <span v-else>{{ r.marca || '—' }}</span>
            </td>
            <!-- sku -->
            <td
              class="border border-border px-2 py-1.5 text-xs font-mono"
              :class="{
                'cursor-pointer': canEdit,
                'ring-2 ring-blue-500 ring-inset bg-background': isEditing(r.id, 'sku'),
                'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(r.id, 'sku'),
              }"
              @click="!isEditing(r.id, 'sku') && startEdit(r, 'sku')"
            >
              <input
                v-if="isEditing(r.id, 'sku')"
                :ref="setEditInputRef"
                v-model="editValue" type="text"
                class="w-full text-xs bg-transparent outline-none font-mono"
                @blur="commitEdit" @keydown.enter.prevent="commitEdit" @keydown.escape.prevent="cancelEdit"
              />
              <span v-else class="break-all">{{ r.sku || '—' }}</span>
            </td>
            <!-- roteiro (célula grande — textarea ao editar) -->
            <td
              class="border border-border px-2 py-1.5 text-xs"
              :class="{
                'cursor-pointer': canEdit,
                'ring-2 ring-blue-500 ring-inset bg-background': isEditing(r.id, 'roteiro'),
                'bg-emerald-50 dark:bg-emerald-900/20': isFlashed(r.id, 'roteiro'),
              }"
              @click="!isEditing(r.id, 'roteiro') && startEdit(r, 'roteiro')"
            >
              <textarea
                v-if="isEditing(r.id, 'roteiro')"
                :ref="setEditInputRef"
                v-model="editValue"
                rows="6"
                class="w-full text-xs bg-transparent outline-none resize-y min-h-[120px] leading-snug"
                placeholder="Escreva o roteiro — cena, fala, texto na tela…"
                @blur="commitEdit" @keydown.escape.prevent="cancelEdit"
              />
              <span v-else-if="r.roteiro" class="block whitespace-pre-wrap leading-snug">{{ r.roteiro }}</span>
              <span v-else class="text-muted-foreground italic">clique pra escrever o roteiro…</span>
            </td>
            <!-- arquivos -->
            <td class="border border-border px-2 py-1.5 text-xs">
              <div class="space-y-1">
                <div v-for="f in r.files" :key="f.id" class="flex items-center gap-1">
                  <button
                    type="button"
                    class="inline-flex min-w-0 items-center gap-1 text-primary hover:underline"
                    :title="`Visualizar ${f.file_name}`"
                    @click.stop="openPreview(r, f)"
                  >
                    <ImageIcon v-if="f.file_mime?.startsWith('image/')" class="size-3.5 shrink-0" />
                    <Film v-else-if="f.file_mime?.startsWith('video/')" class="size-3.5 shrink-0" />
                    <FileIcon v-else class="size-3.5 shrink-0" />
                    <span class="truncate max-w-[140px]">{{ f.file_name }}</span>
                  </button>
                  <span class="text-muted-foreground shrink-0 text-[10px]">{{ fmtSize(f.file_size) }}</span>
                  <button
                    v-if="canEdit && !r.pushed_at"
                    class="shrink-0 rounded p-0.5 text-muted-foreground hover:text-destructive hover:bg-destructive/10"
                    title="Apagar arquivo"
                    @click.stop="removeFile(r, f)"
                  >
                    <X class="size-3" />
                  </button>
                </div>
                <div class="flex items-center gap-1.5">
                  <button
                    v-if="canEdit && !r.pushed_at"
                    class="inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] bg-background hover:bg-muted disabled:opacity-50"
                    :disabled="uploadingId === r.id"
                    @click.stop="pickFile(r)"
                  >
                    <Loader2 v-if="uploadingId === r.id" class="size-3 animate-spin" />
                    <Upload v-else class="size-3" />
                    {{ r.files.length ? 'adicionar' : 'anexar' }}
                  </button>
                  <span
                    v-if="r.pushed_at"
                    class="pill-success"
                    :title="`Enviado pro MEGA: ${r.pushed_dest}`"
                  >
                    <CloudUpload class="size-3" /> no MEGA
                  </span>
                </div>
              </div>
            </td>
            <!-- aprovado -->
            <td class="border border-border px-2 py-1.5 text-center">
              <div v-if="isAdmin" class="inline-flex items-center gap-1">
                <button
                  class="rounded p-1 border transition-colors disabled:opacity-50"
                  :class="r.aprovado === true
                    ? 'bg-emerald-500 text-white border-emerald-500'
                    : 'text-emerald-600 bg-background hover:bg-emerald-500/10'"
                  :disabled="approvingId === r.id"
                  title="Aprovar (envia os arquivos pro MEGA)"
                  @click.stop="aprovar(r, true)"
                >
                  <Loader2 v-if="approvingId === r.id" class="size-3.5 animate-spin" />
                  <Check v-else class="size-3.5" />
                </button>
                <button
                  class="rounded p-1 border transition-colors disabled:opacity-50"
                  :class="r.aprovado === false
                    ? 'bg-red-500 text-white border-red-500'
                    : 'text-red-600 bg-background hover:bg-red-500/10'"
                  :disabled="approvingId === r.id"
                  title="Não aprovar"
                  @click.stop="aprovar(r, false)"
                >
                  <X class="size-3.5" />
                </button>
              </div>
              <span v-else-if="r.aprovado === true" class="pill-success"><Check class="size-3" /> aprovado</span>
              <span v-else-if="r.aprovado === false" class="pill-danger"><X class="size-3" /> não aprovado</span>
              <span v-else class="pill-muted">pendente</span>
            </td>
            <!-- ações -->
            <td class="border border-border px-1 py-1.5 text-center">
              <button
                v-if="canEdit && (!r.pushed_at || isAdmin)"
                class="p-1 text-muted-foreground hover:text-destructive hover:bg-destructive/10 rounded"
                title="Excluir linha"
                @click.stop="remove(r)"
              >
                <Trash2 class="size-3.5" />
              </button>
            </td>
          </tr>

          <!-- add row -->
          <tr v-if="canEdit" class="bg-blue-50/40 dark:bg-blue-900/10">
            <td class="border border-border px-1 py-1">
              <select
                v-model="newRow.modelo"
                class="w-full text-xs border rounded px-1.5 py-1 bg-background"
                :class="{ 'text-muted-foreground': !newRow.modelo }"
              >
                <option value="" disabled>modelo…</option>
                <option v-for="o in MODELO_OPTIONS" :key="o" :value="o">{{ o }}</option>
              </select>
            </td>
            <td class="border border-border px-1 py-1">
              <select
                v-model="newRow.marca"
                class="w-full text-xs border rounded px-1.5 py-1 bg-background"
                :class="{ 'text-muted-foreground': !newRow.marca }"
              >
                <option value="">marca —</option>
                <option v-for="o in MARCA_OPTIONS" :key="o" :value="o">{{ o }}</option>
              </select>
            </td>
            <td class="border border-border px-1 py-1">
              <input
                v-model="newRow.sku"
                type="text" placeholder="SKU"
                class="w-full text-xs border rounded px-1.5 py-1 bg-background font-mono"
                @keydown.enter="addRow"
              />
            </td>
            <td colspan="3" class="border border-border px-2 py-1 text-[11px] text-muted-foreground align-middle">
              Roteiro e arquivos você preenche clicando na célula depois de adicionar.
            </td>
            <td class="border border-border px-1 py-1 text-center">
              <button
                class="inline-flex items-center justify-center rounded bg-primary text-primary-foreground p-1.5 disabled:opacity-50"
                title="Adicionar linha"
                :disabled="adding"
                @click="addRow"
              >
                <Loader2 v-if="adding" class="size-3.5 animate-spin" />
                <Plus v-else class="size-3.5" />
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <p class="text-xs text-muted-foreground">
      Clique numa célula pra editar (Enter salva, Esc cancela). Dá pra anexar vários
      arquivos por linha; clique no nome pra visualizar a imagem ou o vídeo (com opção
      de baixar). Ao aprovar (<Check class="size-3 inline text-emerald-600" />), todos os
      arquivos sobem pra pasta do produto no MEGA — o produto é achado pelo SKU na
      Tabela de Preços (aba Produtos).
    </p>

    <!-- preview modal -->
    <div
      v-if="preview"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4"
      @click.self="closePreview"
    >
      <div class="flex max-h-full w-full max-w-5xl flex-col overflow-hidden rounded-lg border border-border bg-card shadow-xl">
        <div class="flex items-center gap-2 border-b px-3 py-2">
          <ImageIcon v-if="previewIsImage" class="size-4 shrink-0 text-muted-foreground" />
          <Film v-else-if="previewIsVideo" class="size-4 shrink-0 text-muted-foreground" />
          <FileIcon v-else class="size-4 shrink-0 text-muted-foreground" />
          <span class="truncate text-sm font-medium">{{ preview.file.file_name }}</span>
          <span class="shrink-0 text-xs text-muted-foreground">{{ fmtSize(preview.file.file_size) }}</span>
          <div class="ml-auto flex shrink-0 items-center gap-1.5">
            <a
              :href="fileUrl(preview.row, preview.file, true)"
              class="btn btn-sm gap-1"
            >
              <Download class="size-3.5" /> baixar
            </a>
            <button class="btn btn-sm btn-ghost px-1.5" title="Fechar (Esc)" @click="closePreview">
              <X class="size-4" />
            </button>
          </div>
        </div>
        <div class="flex min-h-[200px] flex-1 items-center justify-center overflow-auto bg-black/40 p-2">
          <img
            v-if="previewIsImage"
            :src="fileUrl(preview.row, preview.file)"
            :alt="preview.file.file_name"
            class="max-h-[75vh] max-w-full object-contain"
          />
          <video
            v-else-if="previewIsVideo"
            :src="fileUrl(preview.row, preview.file)"
            controls
            autoplay
            class="max-h-[75vh] max-w-full"
          />
          <div v-else class="p-8 text-center text-sm text-muted-foreground">
            Não dá pra visualizar esse tipo de arquivo aqui — use o botão baixar.
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
