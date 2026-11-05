<script setup lang="ts">
import { Trash2 } from 'lucide-vue-next'

type Item = { id: string; subject: string; from_address: string; received_at: string }
const props = defineProps<{ mailboxId: string; disabled?: boolean }>()
const emit = defineEmits<{ (e: 'restaurado'): void }>()
const { api } = useApi()
const open = ref(false)
const items = ref<Item[]>([])
const busy = ref(false)
const more = ref(false)
const error = ref('')
async function load(append = false) {
  if (busy.value || props.disabled) return
  busy.value = true; error.value = ''
  try {
    const result = await api<{ items: Item[]; more: boolean }>(`/api/mail/mailboxes/${encodeURIComponent(props.mailboxId)}/trash`, { query: { offset: append ? items.value.length : 0 }, retry: 0 })
    items.value = append ? [...items.value, ...result.items] : result.items
    more.value = result.more
  } catch { error.value = 'Não foi possível abrir a lixeira. Atualize e confira seu acesso à caixa.' }
  finally { busy.value = false }
}
async function toggle() {
  if (busy.value || props.disabled) return
  open.value = !open.value
  if (open.value) await load()
}
async function restore(item: Item) {
  if (busy.value || props.disabled) return
  busy.value = true; error.value = ''
  try {
    await api(`/api/mail/messages/${encodeURIComponent(item.id)}/restore`, { method: 'POST' })
    items.value = items.value.filter((row) => row.id !== item.id)
    emit('restaurado')
  } catch { error.value = 'Não foi possível restaurar o e-mail. Atualize e confira seu acesso à caixa.' }
  finally { busy.value = false }
}
</script>

<template>
  <section class="rounded-lg border p-3" data-mail-trash>
    <button type="button" class="inline-flex items-center gap-2 text-sm" :aria-expanded="open" :disabled="disabled || busy" @click="toggle"><Trash2 class="size-4" /> Lixeira desta caixa</button>
    <div v-if="open" class="mt-3 space-y-3">
      <p class="text-xs text-muted-foreground">E-mails excluídos do DaVinci. Restaurar devolve a mensagem à caixa; o original continua no Tuta.</p>
      <p v-if="error" role="alert" class="text-sm text-destructive">{{ error }}</p>
      <p v-if="!busy && !items.length && !error" class="text-sm text-muted-foreground">Nenhum e-mail na lixeira desta caixa.</p>
      <ul class="divide-y">
        <li v-for="item in items" :key="item.id" class="flex items-center justify-between gap-3 py-2">
          <div class="min-w-0"><p class="truncate text-sm">{{ item.subject || '(Sem assunto)' }}</p><p class="truncate text-xs text-muted-foreground">{{ item.from_address }}</p></div>
          <button type="button" class="shrink-0 rounded border px-3 py-1 text-xs" :disabled="disabled || busy" @click="restore(item)">Restaurar</button>
        </li>
      </ul>
      <button v-if="more" type="button" class="rounded border px-3 py-1 text-xs" :disabled="disabled || busy" @click="load(true)">Carregar mais</button>
      <p v-if="busy" role="status" class="text-xs text-muted-foreground">Atualizando…</p>
    </div>
  </section>
</template>
