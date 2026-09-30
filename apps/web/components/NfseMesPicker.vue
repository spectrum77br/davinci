<script setup lang="ts">
// Seletor de mês de competência ('AAAA-MM'; '' = todos os meses): ‹ › para
// andar um mês e um popover com a grade dos 12 meses. Substitui o seletor
// de mês nativo do navegador.
import { computed, ref, watch } from 'vue'
import { PopoverContent, PopoverPortal, PopoverRoot, PopoverTrigger } from 'reka-ui'
import { CalendarDays, ChevronLeft, ChevronRight } from 'lucide-vue-next'
import { Button } from '~/components/ui/button'
import { MESES, mesAtual, mesValido, nomeMes, somarMes } from '~/lib/nfse'

const props = withDefaults(
  defineProps<{
    modelValue: string
    permitirTodos?: boolean
    min?: string
    max?: string
    disabled?: boolean
  }>(),
  { permitirTodos: false, disabled: false },
)

const emit = defineEmits<{ (e: 'update:modelValue', v: string): void }>()

const aberto = ref(false)
const hoje = mesAtual()
const anoVisto = ref(Number((mesValido(props.modelValue) ? props.modelValue : hoje).slice(0, 4)))

watch(aberto, (v) => {
  if (v) anoVisto.value = Number((mesValido(props.modelValue) ? props.modelValue : hoje).slice(0, 4))
})

const ABREV = MESES.map((m) => m.slice(0, 3))

function capitalizar(s: string) {
  return s ? s[0]!.toUpperCase() + s.slice(1) : s
}

const rotulo = computed(() => (mesValido(props.modelValue) ? capitalizar(nomeMes(props.modelValue)) : 'Todos os meses'))

function foraDoLimite(mes: string): boolean {
  if (props.min && mes < props.min.slice(0, 7)) return true
  if (props.max && mes > props.max.slice(0, 7)) return true
  return false
}

function escolher(mes: string) {
  if (foraDoLimite(mes)) return
  if (mes !== props.modelValue) emit('update:modelValue', mes)
  aberto.value = false
}

function andar(delta: number) {
  if (!mesValido(props.modelValue)) return escolher(hoje)
  const alvo = somarMes(props.modelValue, delta)
  if (!foraDoLimite(alvo)) emit('update:modelValue', alvo)
}

const podeVoltar = computed(
  () => !props.disabled && (!mesValido(props.modelValue) || !foraDoLimite(somarMes(props.modelValue, -1))),
)
const podeAvancar = computed(
  () => !props.disabled && (!mesValido(props.modelValue) || !foraDoLimite(somarMes(props.modelValue, 1))),
)

const anoMin = computed(() => (props.min ? Number(props.min.slice(0, 4)) : -Infinity))
const anoMax = computed(() => (props.max ? Number(props.max.slice(0, 4)) : Infinity))

function mesDaGrade(i: number): string {
  return `${anoVisto.value}-${String(i + 1).padStart(2, '0')}`
}
</script>

<template>
  <div class="inline-flex items-center gap-1">
    <Button
      variant="outline"
      size="icon"
      class="size-9"
      aria-label="mês anterior"
      :disabled="!podeVoltar"
      @click="andar(-1)"
    >
      <ChevronLeft class="size-4" />
    </Button>

    <PopoverRoot v-model:open="aberto">
      <PopoverTrigger as-child :disabled="disabled">
        <Button variant="outline" size="sm" class="min-w-[176px] justify-start" :disabled="disabled" aria-haspopup="dialog">
          <CalendarDays class="mr-1.5 size-4 text-muted-foreground" aria-hidden="true" />
          {{ rotulo }}
        </Button>
      </PopoverTrigger>
      <PopoverPortal>
        <PopoverContent
          side="bottom"
          align="start"
          :side-offset="4"
          :collision-padding="8"
          class="z-[80] w-64 rounded-md border bg-background p-3 shadow-lg duration-150 data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=open]:fade-in-0 data-[state=closed]:fade-out-0 motion-reduce:animate-none"
        >
          <div class="mb-2 flex items-center justify-between">
            <Button
              variant="ghost"
              size="icon"
              class="size-7"
              aria-label="ano anterior"
              :disabled="anoVisto - 1 < anoMin"
              @click="anoVisto--"
            >
              <ChevronLeft class="size-4" />
            </Button>
            <span class="text-sm font-medium tabular-nums">{{ anoVisto }}</span>
            <Button
              variant="ghost"
              size="icon"
              class="size-7"
              aria-label="próximo ano"
              :disabled="anoVisto + 1 > anoMax"
              @click="anoVisto++"
            >
              <ChevronRight class="size-4" />
            </Button>
          </div>

          <div class="grid grid-cols-3 gap-1" role="listbox" :aria-label="`meses de ${anoVisto}`">
            <button
              v-for="(m, i) in ABREV"
              :key="m"
              type="button"
              role="option"
              :aria-selected="modelValue === mesDaGrade(i)"
              :aria-label="nomeMes(mesDaGrade(i))"
              :disabled="foraDoLimite(mesDaGrade(i))"
              class="h-8 rounded text-sm capitalize transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-40"
              :class="[
                modelValue === mesDaGrade(i)
                  ? 'bg-primary text-primary-foreground'
                  : 'hover:bg-muted',
                mesDaGrade(i) === hoje && modelValue !== mesDaGrade(i) && 'ring-1 ring-primary/40',
              ]"
              @click="escolher(mesDaGrade(i))"
            >
              {{ m }}
            </button>
          </div>

          <div class="mt-2 flex justify-between border-t pt-2">
            <Button
              variant="link"
              class="h-auto p-0 text-xs"
              :disabled="foraDoLimite(hoje)"
              @click="escolher(hoje)"
            >este mês</Button>
            <Button
              v-if="permitirTodos"
              variant="link"
              class="h-auto p-0 text-xs"
              @click="emit('update:modelValue', ''); aberto = false"
            >todos os meses</Button>
          </div>
        </PopoverContent>
      </PopoverPortal>
    </PopoverRoot>

    <Button
      variant="outline"
      size="icon"
      class="size-9"
      aria-label="próximo mês"
      :disabled="!podeAvancar"
      @click="andar(1)"
    >
      <ChevronRight class="size-4" />
    </Button>
  </div>
</template>
