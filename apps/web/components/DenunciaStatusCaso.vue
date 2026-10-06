<script setup lang="ts">
import { PopoverContent, PopoverPortal, PopoverRoot, PopoverTrigger } from 'reka-ui'
import { Check, ChevronDown, Hand, Loader2 } from 'lucide-vue-next'
import { STATUS_CASO, hojeBr, pillStatusCaso } from '~/lib/denuncia'

// Status do caso com troca à mão (06/10/2026, Vinicius: "clicar no status e conseguir trocar — aparecer
// todos os status possíveis"). O status sai dos fatos (compra, entrega, envio ao advogado); o escolhido
// aqui vale por cima até alguém voltar ao automático. "Com jurídico" pede a data do envio ao advogado
// quando o caso ainda não tem. Quem só vê enxerga o selo, sem a lista.
const props = withDefaults(defineProps<{
  status: string | null
  // o que os fatos dizem (aparece quando alguém trocou à mão)
  auto?: string | null
  manualPor?: string | null
  manualEm?: string | null
  juridicoEm?: string | null
  editavel?: boolean
  salvando?: boolean
}>(), { auto: null, manualPor: null, manualEm: null, juridicoEm: null, editavel: false, salvando: false })

const emit = defineEmits<{ (e: 'trocar', status: string | null, juridicoData: string | null): void }>()

const aberto = ref(false)
const pedindoData = ref(false)
const data = ref('')
const manual = computed(() => !!props.manualPor)

function quando(iso: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleDateString('pt-BR', { timeZone: 'America/Sao_Paulo', day: '2-digit', month: '2-digit' })
}
const titulo = computed(() =>
  manual.value
    ? `trocado à mão por ${props.manualPor}${props.manualEm ? ` em ${quando(props.manualEm)}` : ''} — pelos fatos seria "${props.auto || '—'}"`
    : 'status pelos fatos (compra, entrega, envio ao advogado)',
)

function setOpen(v: boolean) {
  aberto.value = v
  if (!v) pedindoData.value = false
}
function escolher(s: string | null) {
  if (s === 'Com jurídico' && !props.juridicoEm) {
    data.value = hojeBr()
    pedindoData.value = true
    return
  }
  setOpen(false)
  if (s === null ? !manual.value : s === props.status && manual.value) return
  emit('trocar', s, null)
}
function confirmarData() {
  if (!data.value) return
  setOpen(false)
  emit('trocar', 'Com jurídico', data.value)
}
</script>

<template>
  <span v-if="!editavel" class="inline-flex items-center gap-0.5 whitespace-nowrap !px-2.5 !text-xs" :class="pillStatusCaso(status)" :title="titulo">
    {{ status || '—' }}<Hand v-if="manual" class="size-3" aria-label="trocado à mão" />
  </span>
  <PopoverRoot v-else :open="aberto" @update:open="setOpen">
    <PopoverTrigger as-child>
      <button
        type="button"
        class="inline-flex items-center gap-0.5 whitespace-nowrap !px-2.5 !text-xs hover:ring-1 hover:ring-primary/40 disabled:opacity-60"
        :class="pillStatusCaso(status)"
        :title="`${titulo} — clique para trocar`"
        :disabled="salvando"
      >
        {{ status || '—' }}
        <Loader2 v-if="salvando" class="size-3 animate-spin motion-reduce:animate-none" />
        <Hand v-else-if="manual" class="size-3" aria-label="trocado à mão" />
        <ChevronDown class="size-3 opacity-60" />
      </button>
    </PopoverTrigger>
    <PopoverPortal>
      <PopoverContent
        side="bottom"
        align="center"
        :side-offset="4"
        :collision-padding="8"
        class="z-[70] w-60 max-w-[calc(100vw-16px)] rounded-md border bg-background p-1 text-xs shadow-lg"
        @click.stop
      >
        <template v-if="!pedindoData">
          <div class="px-2 pb-1 pt-1.5 text-[11px] font-medium text-muted-foreground">Trocar o status</div>
          <button
            v-for="s in STATUS_CASO"
            :key="s"
            type="button"
            class="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left hover:bg-muted"
            @click="escolher(s)"
          >
            <span class="whitespace-nowrap" :class="pillStatusCaso(s)">{{ s }}</span>
            <Check v-if="s === status" class="ml-auto size-3.5 text-primary" aria-label="atual" />
          </button>
          <template v-if="manual">
            <div class="my-1 border-t" />
            <button type="button" class="w-full rounded px-2 py-1.5 text-left hover:bg-muted" @click="escolher(null)">
              Voltar ao automático
              <span class="block text-[11px] text-muted-foreground">pelos fatos: {{ auto || '—' }}</span>
            </button>
          </template>
        </template>
        <form v-else class="space-y-2 p-2" @submit.prevent="confirmarData">
          <label class="block space-y-1">
            <span class="text-[11px] font-medium text-muted-foreground">Enviado ao advogado em</span>
            <input v-model="data" type="date" required class="h-8 w-full rounded-md border bg-background px-2 text-xs">
          </label>
          <div class="flex justify-end gap-2">
            <Button type="button" size="sm" variant="ghost" class="h-7 px-2 text-xs" @click="pedindoData = false">voltar</Button>
            <Button type="submit" size="sm" class="h-7 px-3 text-xs" :disabled="!data">Com jurídico</Button>
          </div>
        </form>
      </PopoverContent>
    </PopoverPortal>
  </PopoverRoot>
</template>
