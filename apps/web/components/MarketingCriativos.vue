<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
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

const { api } = useApi()
const toasts = useToasts()
const auth = useAuthStore()
const isAdmin = computed(() => auth.user?.role === 'admin')
const canEdit = useCan('marketing_criativos', 'edit')

const cellCls =
  'rounded-md border bg-background px-2 py-1.5 text-sm focus:outline-none '
  + 'focus:ring-1 focus:ring-primary disabled:bg-transparent '
  + 'disabled:border-transparent disabled:cursor-default'

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

// ---- add row -------------------------------------------------------------
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

// ---- inline edit (salva ao sair do campo) --------------------------------
async function saveField(r: Creative, field: 'modelo' | 'marca' | 'sku' | 'roteiro') {
  try {
    const updated = await api<Creative>(`/api/marketing/creatives/${r.id}`, {
      method: 'PATCH',
      body: { [field]: r[field] },
    })
    Object.assign(r, updated)
  } catch (e: any) {
    toasts.error('Erro ao salvar', errMsg(e))
  }
}

// ---- arquivo -------------------------------------------------------------
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

// ---- aprovação (admin) ---------------------------------------------------
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

// ---- excluir -------------------------------------------------------------
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
  <div class="space-y-4">
    <input
      ref="fileInput"
      type="file"
      accept="image/*,video/*"
      class="hidden"
      @change="onFilePicked"
    >

    <div class="table-card overflow-x-auto">
      <table class="w-full min-w-[980px]">
        <thead>
          <tr>
            <th class="w-[15%]">Modelo</th>
            <th class="w-[10%]">Marca</th>
            <th class="w-[12%]">SKU</th>
            <th class="w-[33%]">Roteiro</th>
            <th class="w-[18%]">Arquivo</th>
            <th class="w-[8%] text-center">Aprovado</th>
            <th class="w-[4%]" />
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading && rows.length === 0">
            <td colspan="7" class="py-8 text-center text-muted-foreground">
              <Loader2 class="size-4 animate-spin inline mr-2" />carregando…
            </td>
          </tr>
          <tr v-else-if="rows.length === 0">
            <td colspan="7" class="py-8 text-center text-muted-foreground">
              Nenhum criativo ainda — adicione a primeira linha abaixo.
            </td>
          </tr>

          <tr v-for="r in rows" :key="r.id" class="align-top">
            <td>
              <input
                v-model="r.modelo"
                :disabled="!canEdit"
                :class="cellCls" class="w-full"
                placeholder="ex.: imagem lifestyle"
                @blur="saveField(r, 'modelo')"
              >
            </td>
            <td>
              <input
                v-model="r.marca"
                :disabled="!canEdit"
                :class="cellCls" class="w-full"
                placeholder="marca"
                @blur="saveField(r, 'marca')"
              >
            </td>
            <td>
              <input
                v-model="r.sku"
                :disabled="!canEdit"
                :class="cellCls" class="w-full font-mono text-xs"
                placeholder="SKU do produto"
                @blur="saveField(r, 'sku')"
              >
            </td>
            <td>
              <textarea
                v-model="r.roteiro"
                :disabled="!canEdit"
                rows="4"
                :class="cellCls" class="w-full resize-y min-h-[90px] leading-snug"
                placeholder="Escreva o roteiro aqui — espaço livre pra detalhar cena, fala, texto na tela…"
                @blur="saveField(r, 'roteiro')"
              />
            </td>
            <td>
              <div class="space-y-1.5">
                <div v-if="r.file_name" class="flex items-center gap-1.5 text-xs">
                  <a
                    :href="`/api/marketing/creatives/${r.id}/arquivo`"
                    target="_blank"
                    class="inline-flex items-center gap-1 text-primary hover:underline break-all"
                    :title="r.file_name"
                  >
                    <Download class="size-3.5 shrink-0" />
                    <span class="truncate max-w-[160px]">{{ r.file_name }}</span>
                  </a>
                  <span class="text-muted-foreground shrink-0">{{ fmtSize(r.file_size) }}</span>
                </div>
                <div class="flex items-center gap-1.5">
                  <button
                    v-if="canEdit && !r.pushed_at"
                    class="inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs hover:bg-muted disabled:opacity-50"
                    :disabled="uploadingId === r.id"
                    @click="pickFile(r)"
                  >
                    <Loader2 v-if="uploadingId === r.id" class="size-3.5 animate-spin" />
                    <Upload v-else class="size-3.5" />
                    {{ r.file_name ? 'trocar' : 'anexar' }}
                  </button>
                  <span
                    v-if="r.pushed_at"
                    class="inline-flex items-center gap-1 rounded-md bg-emerald-500/10 text-emerald-600 px-2 py-1 text-[11px] font-medium"
                    :title="`Enviado pro MEGA: ${r.pushed_dest}`"
                  >
                    <CloudUpload class="size-3.5" /> no MEGA
                  </span>
                </div>
              </div>
            </td>
            <td class="text-center">
              <div v-if="isAdmin" class="inline-flex items-center gap-1">
                <button
                  class="rounded-md p-1.5 border transition-colors disabled:opacity-50"
                  :class="r.aprovado === true
                    ? 'bg-emerald-500 text-white border-emerald-500'
                    : 'text-emerald-600 hover:bg-emerald-500/10'"
                  :disabled="approvingId === r.id"
                  title="Aprovar (envia pro MEGA)"
                  @click="aprovar(r, true)"
                >
                  <Loader2 v-if="approvingId === r.id" class="size-4 animate-spin" />
                  <Check v-else class="size-4" />
                </button>
                <button
                  class="rounded-md p-1.5 border transition-colors disabled:opacity-50"
                  :class="r.aprovado === false
                    ? 'bg-red-500 text-white border-red-500'
                    : 'text-red-600 hover:bg-red-500/10'"
                  :disabled="approvingId === r.id"
                  title="Não aprovar"
                  @click="aprovar(r, false)"
                >
                  <X class="size-4" />
                </button>
              </div>
              <span
                v-else
                class="inline-flex items-center justify-center rounded-md px-2 py-1 text-[11px] font-medium"
                :class="r.aprovado === true
                  ? 'bg-emerald-500/10 text-emerald-600'
                  : r.aprovado === false
                    ? 'bg-red-500/10 text-red-600'
                    : 'bg-muted text-muted-foreground'"
              >
                <template v-if="r.aprovado === true"><Check class="size-3.5 mr-1" /> aprovado</template>
                <template v-else-if="r.aprovado === false"><X class="size-3.5 mr-1" /> não aprovado</template>
                <template v-else>pendente</template>
              </span>
            </td>
            <td class="text-right">
              <button
                v-if="canEdit && (!r.pushed_at || isAdmin)"
                class="rounded-md p-1.5 text-muted-foreground hover:text-red-600 hover:bg-red-500/10"
                title="Excluir linha"
                @click="remove(r)"
              >
                <Trash2 class="size-4" />
              </button>
            </td>
          </tr>

          <tr v-if="canEdit" class="bg-muted/30">
            <td>
              <input
                v-model="newRow.modelo"
                :class="cellCls" class="w-full"
                placeholder="novo: ex. video 30s problema"
                @keyup.enter="addRow"
              >
            </td>
            <td>
              <input v-model="newRow.marca" :class="cellCls" class="w-full" placeholder="marca" @keyup.enter="addRow">
            </td>
            <td>
              <input v-model="newRow.sku" :class="cellCls" class="w-full font-mono text-xs" placeholder="SKU" @keyup.enter="addRow">
            </td>
            <td colspan="3" class="text-xs text-muted-foreground align-middle">
              Roteiro e arquivo você preenche depois de adicionar a linha.
            </td>
            <td class="text-right">
              <button
                class="inline-flex items-center gap-1 rounded-md bg-primary text-primary-foreground px-2.5 py-1.5 text-xs font-medium disabled:opacity-50"
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
      Ao aprovar (<Check class="size-3 inline text-emerald-600" />), o arquivo sobe automaticamente
      pra pasta do produto no MEGA — o produto é achado pelo SKU na Tabela de Preços (aba Produtos).
    </p>
  </div>
</template>

