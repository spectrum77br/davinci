<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import {
  Check, X, Trash2, Upload, Loader2, Plus, Download, CloudUpload,
} from 'lucide-vue-next'

type Creative = {
  id: string
  modelo: string
  marca: string | null
  sku: string | null
  roteiro: string | null
  file_name: string | null
  file_mime: string | null
  file_size: number | null
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

const rows = ref<Creative[]>([])
const loading = ref(false)
const uploadingId = ref<string | null>(null)
const approvingId = ref<string | null>(null)

const ERR_PT: Record<string, string> = {
  ja_enviado_pro_mega: 'Esse arquivo já foi pro MEGA — não dá mais pra trocar ou excluir.',
  sem_arquivo: 'Falta anexar o arquivo antes de aprovar.',
  sem_sku: 'Preencha o SKU antes de aprovar.',
  produto_nao_encontrado: 'Nenhum produto na tabela de preços tem esse SKU.',
  produto_sem_pasta: 'O produto desse SKU ainda não tem pasta no MEGA.',
  modelo_obrigatorio: 'O campo modelo é obrigatório.',
  arquivo_sumiu: 'O arquivo não foi encontrado no servidor — anexe de novo.',
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
    toasts.warning('Preencha o modelo (ex.: imagem lifestyle, video 15s...)')
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

// ---- arquivo --------------------------------------------------------------
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
  const file = input.files?.[0]
  const target = fileTarget.value
  input.value = ''
  if (!file || !target) return
  uploadingId.value = target.id
  try {
    const fd = new FormData()
    fd.append('file', file)
    const updated = await api<Creative>(
      `/api/marketing/creatives/${target.id}/arquivo`,
      { method: 'POST', body: fd },
    )
    Object.assign(target, updated)
    toasts.success('Arquivo anexado', file.name)
  } catch (e: any) {
    toasts.error('Erro no upload', errMsg(e))
  } finally {
    uploadingId.value = null
  }
}

function fmtSize(n: number | null): string {
  if (!n) return ''
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`
  return `${(n / (1024 * 1024)).toFixed(1)} MB`
}

// ---- aprovação (admin) ----------------------------------------------------
async function aprovar(r: Creative, ok: boolean) {
  approvingId.value = r.id
  const t = ok && !r.pushed_at
    ? toasts.push({ kind: 'info', title: 'Aprovando…', lines: 'Enviando o arquivo pra pasta do produto no MEGA.' }, 0)
    : null
  try {
    const updated = await api<Creative & { fotos_count?: number | null }>(
      `/api/marketing/creatives/${r.id}/aprovar`,
      { method: 'POST', body: { aprovado: ok } },
    )
    Object.assign(r, updated)
    if (ok && updated.pushed_dest) {
      toasts.success('Aprovado — enviado pro MEGA', `Pasta: ${updated.pushed_dest}`)
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
      class="hidden"
      @change="onFilePicked"
    >

    <div class="border rounded-lg overflow-auto max-h-[calc(100vh-260px)]">
      <table class="w-full text-sm border-collapse">
        <thead class="sticky top-0 bg-muted z-10">
          <tr>
            <th class="text-left px-2 py-2 font-medium border-b border-border min-w-[170px]">Modelo</th>
            <th class="text-left px-2 py-2 font-medium border-b border-border min-w-[110px]">Marca</th>
            <th class="text-left px-2 py-2 font-medium border-b border-border min-w-[150px]">SKU</th>
            <th class="text-left px-2 py-2 font-medium border-b border-border min-w-[340px]">Roteiro</th>
            <th class="text-left px-2 py-2 font-medium border-b border-border min-w-[180px]">Arquivo</th>
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

          <!-- data rows -->
          <tr v-for="r in rows" :key="r.id" class="hover:bg-accent/30 align-top">
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
              <input
                v-if="isEditing(r.id, 'modelo')"
                :ref="setEditInputRef"
                v-model="editValue" type="text"
                class="w-full text-xs bg-transparent outline-none"
                @blur="commitEdit" @keydown.enter.prevent="commitEdit" @keydown.escape.prevent="cancelEdit"
              />
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
              <input
                v-if="isEditing(r.id, 'marca')"
                :ref="setEditInputRef"
                v-model="editValue" type="text"
                class="w-full text-xs bg-transparent outline-none"
                @blur="commitEdit" @keydown.enter.prevent="commitEdit" @keydown.escape.prevent="cancelEdit"
              />
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
              <span v-else>{{ r.sku || '—' }}</span>
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
            <!-- arquivo -->
            <td class="border border-border px-2 py-1.5 text-xs">
              <div class="space-y-1">
                <div v-if="r.file_name" class="flex items-center gap-1.5">
                  <a
                    :href="`/api/marketing/creatives/${r.id}/arquivo`"
                    target="_blank"
                    class="inline-flex items-center gap-1 text-primary hover:underline"
                    :title="r.file_name"
                  >
                    <Download class="size-3.5 shrink-0" />
                    <span class="truncate max-w-[130px]">{{ r.file_name }}</span>
                  </a>
                  <span class="text-muted-foreground shrink-0">{{ fmtSize(r.file_size) }}</span>
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
                    {{ r.file_name ? 'trocar' : 'anexar' }}
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
                  title="Aprovar (envia pro MEGA)"
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
              <input
                v-model="newRow.modelo"
                type="text" placeholder="novo modelo — ex. video 30s problema"
                class="w-full text-xs border rounded px-1.5 py-1 bg-background"
                @keydown.enter="addRow"
              />
            </td>
            <td class="border border-border px-1 py-1">
              <input
                v-model="newRow.marca"
                type="text" placeholder="marca"
                class="w-full text-xs border rounded px-1.5 py-1 bg-background"
                @keydown.enter="addRow"
              />
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
              Roteiro e arquivo você preenche clicando na célula depois de adicionar.
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
      Clique numa célula pra editar (Enter salva, Esc cancela). Ao aprovar
      (<Check class="size-3 inline text-emerald-600" />), o arquivo sobe automaticamente
      pra pasta do produto no MEGA — o produto é achado pelo SKU na Tabela de Preços (aba Produtos).
    </p>
  </div>
</template>
