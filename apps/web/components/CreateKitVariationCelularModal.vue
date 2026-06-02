<script setup lang="ts">
// Modal pra criar variação de kit em Celular. Padrão estrito:
// `aXXX` ou combinação `aXXX+aYYY+...`. Espelho do backend.
//
// Helper text e regex visíveis pro operador — separado do modal de
// Mala pra cada categoria evoluir suas regras sem checar `if`.
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

// `^a\d+(\+a\d+)*$` espelha o backend. Avisa o operador antes do submit.
const CELULAR_CODE_RE = /^a\d+(\+a\d+)*$/

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
  if (!CELULAR_CODE_RE.test(c)) {
    err.value = "Use formato 'aXXX' ou 'aXXX+aYYY' (ex: a001, a003+a004)"
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
      { method: 'POST', body: { categoria: 'celular', code: c, label: l } },
    )
    emit('created', created)
    reset()
    emit('close')
  } catch (e: any) {
    if (e?.response?.status === 409 || e?.statusCode === 409) {
      err.value = 'Já existe variação celular com esse código'
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
        <h3 class="font-semibold text-sm">Criar Kit (Celular)</h3>
        <button class="text-muted-foreground hover:text-foreground" @click="emit('close')">
          <X class="size-4" />
        </button>
      </div>
      <div class="p-4 space-y-3 text-sm">
        <label class="flex flex-col gap-1">
          <span class="text-[10px] text-muted-foreground">Nome do kit *</span>
          <input v-model="label" type="text"
            class="h-8 border rounded px-2 bg-background"
            placeholder="ex: Capa" maxlength="60" />
        </label>
        <label class="flex flex-col gap-1">
          <span class="text-[10px] text-muted-foreground">Código *</span>
          <input v-model="code" type="text"
            class="h-8 border rounded px-2 bg-background font-mono"
            placeholder="ex: a005 ou a001+a005" />
        </label>
        <div class="text-[10px] text-muted-foreground bg-muted/40 rounded p-2">
          Use código de acessório (<code>aXXX</code>) ou combinação
          (<code>a001+a005</code>). Acessórios precisam estar cadastrados em
          <code>products</code> com o mesmo SKU pra entrar como componente do kit.
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
