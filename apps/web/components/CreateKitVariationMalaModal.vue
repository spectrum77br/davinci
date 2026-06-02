<script setup lang="ts">
// Modal pra criar variação de kit em Mala. Padrão: tamanhos numéricos
// separados por '+' ou '.' ou ',', com acessórios (aXXX, bpYYY...)
// opcionais misturados. Espelha parse_kit_variation do backend.
//
// Helper text e regex visíveis pro operador — separado do modal de
// Celular pra cada categoria evoluir suas regras sem checar `if`.
import { ref } from 'vue'
import { X } from 'lucide-vue-next'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{
  (e: 'close'): void
  (e: 'created', variation: { id: string; code: string; label: string; ordem: number }): void
}>()

const { api } = useApi()

const code = ref('')
const label = ref('')
const submitting = ref(false)
const err = ref<string | null>(null)

// Mala: requer ao menos 1 tamanho NUMÉRICO. Espelha
// parse_kit_variation: split por +/, e checa se há alguma parte
// composta só de dígitos. Acessórios (letras) opcionais.
function malaCodeOk(c: string): boolean {
  if (!c.trim()) return false
  const parts = c.split(/[+,]/).map((p) => p.trim()).filter(Boolean)
  if (parts.length === 0) return false
  return parts.some((p) => /^\d+$/.test(p))
}

function reset() {
  code.value = ''
  label.value = ''
  err.value = null
  submitting.value = false
}

async function submit() {
  err.value = null
  const c = code.value.trim()
  const l = label.value.trim()
  if (!malaCodeOk(c)) {
    err.value = "Mala requer pelo menos 1 tamanho numérico (ex: 8, 12+20, 8+12+a075)"
    return
  }
  if (!l) {
    err.value = 'Nome obrigatório'
    return
  }
  if (l.length > 60) {
    err.value = 'Nome muito longo (máx 60 caracteres)'
    return
  }
  submitting.value = true
  try {
    const created = await api<{ id: string; code: string; label: string; ordem: number }>(
      '/api/importacao/kit/variations',
      { method: 'POST', body: { categoria: 'mala', code: c, label: l } },
    )
    emit('created', created)
    reset()
    emit('close')
  } catch (e: any) {
    if (e?.response?.status === 409 || e?.statusCode === 409) {
      err.value = 'Já existe variação mala com esse código'
    } else {
      err.value = e?.data?.detail?.message
        || e?.data?.detail?.code
        || 'Falha ao criar variação'
    }
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div v-if="props.open"
    class="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4"
    @click.self="emit('close')">
    <div class="bg-background rounded-lg shadow-xl w-full max-w-md">
      <div class="flex items-center justify-between border-b px-4 py-3">
        <h3 class="font-semibold text-sm">Criar Kit (Mala)</h3>
        <button class="text-muted-foreground hover:text-foreground" @click="emit('close')">
          <X class="size-4" />
        </button>
      </div>
      <div class="p-4 space-y-3 text-sm">
        <label class="flex flex-col gap-1">
          <span class="text-[10px] text-muted-foreground">Nome do kit *</span>
          <input v-model="label" type="text"
            class="h-8 border rounded px-2 bg-background"
            placeholder="ex: M1+M3 12+20" maxlength="60" />
        </label>
        <label class="flex flex-col gap-1">
          <span class="text-[10px] text-muted-foreground">Código *</span>
          <input v-model="code" type="text"
            class="h-8 border rounded px-2 bg-background font-mono"
            placeholder="ex: 12+20" />
        </label>
        <div class="text-[10px] text-muted-foreground bg-muted/40 rounded p-2">
          Use tamanhos numéricos separados por <code>+</code> ou <code>,</code>
          (ex: <code>8</code>, <code>12+20</code>, <code>8+12+18</code>) ou
          inclua acessórios depois (ex: <code>12+20+a075</code>).
        </div>
        <div v-if="err" class="text-xs text-destructive">{{ err }}</div>
      </div>
      <div class="flex gap-2 justify-end border-t px-4 py-3">
        <button class="rounded-md border px-3 py-1 text-sm"
          @click="emit('close')" :disabled="submitting">Cancelar</button>
        <button class="rounded-md bg-primary text-primary-foreground px-3 py-1 text-sm disabled:opacity-50"
          @click="submit" :disabled="submitting">
          {{ submitting ? 'Criando…' : 'Criar Kit' }}
        </button>
      </div>
    </div>
  </div>
</template>
