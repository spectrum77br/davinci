<script setup lang="ts">
import { ChevronDown } from 'lucide-vue-next'
import { PopoverContent, PopoverPortal, PopoverRoot, PopoverTrigger } from 'reka-ui'
import { hojeISO } from '~/lib/conciliacao'
import { REVISAO_DOT, diaMes, rotuloRevisao, somarDias } from '~/lib/chamadosRevisao'
import type { RevisaoCampos, RevisaoMarca, RevisaoTipo } from '~/lib/chamadosRevisao'

// Seletor da coluna Revisão (Vinicius 05/11: "um botão só onde eu clico e escolho").
// Fixar abre a data e o motivo na mesma caixinha, sem janela nova. Quem só vê não abre.
const props = withDefaults(defineProps<{
  revisao: RevisaoCampos
  disabled?: boolean
  largo?: boolean
}>(), { disabled: false, largo: false })

const emit = defineEmits<{ (e: 'marcar', marca: RevisaoMarca): void }>()

const OPCOES: { tipo: RevisaoTipo; label: string; dica: string; dot: string }[] = [
  { tipo: 'conferido', label: 'Conferido', dica: 'some amanhã', dot: REVISAO_DOT.conferido },
  { tipo: 'revisar', label: 'Revisar', dica: 'some amanhã', dot: REVISAO_DOT.revisar },
]
const ATALHOS = [7, 15, 30]

const open = ref(false)
const fixando = ref(false)
const ate = ref('')
const motivo = ref('')
const erro = ref('')

const rotulo = computed(() => rotuloRevisao(props.revisao))
const vazio = computed(() => props.revisao.revisao_estado === 'falta')
const amanha = computed(() => somarDias(hojeISO(), 1))

function setOpen(v: boolean) {
  open.value = v
  if (v) {
    fixando.value = false
    erro.value = ''
  }
}

function escolher(tipo: RevisaoTipo | null) {
  open.value = false
  emit('marcar', { tipo })
}

// Fixo que caiu (a plataforma respondeu, ou a data passou) volta com o motivo já
// escrito: quase sempre é só empurrar a data.
function abrirFixar() {
  const atual = props.revisao.revisao_ate
  ate.value = atual && atual >= amanha.value ? atual : somarDias(hojeISO(), 7)
  motivo.value = props.revisao.revisao_motivo || ''
  erro.value = ''
  fixando.value = true
}

function fixar() {
  const texto = motivo.value.trim()
  if (!ate.value || ate.value < amanha.value) {
    erro.value = 'Escolha uma data depois de hoje.'
    return
  }
  if (!texto) {
    erro.value = 'Escreva o motivo, ele volta quando a data chegar.'
    return
  }
  open.value = false
  emit('marcar', { tipo: 'fixo', ate: ate.value, motivo: texto })
}
</script>

<template>
  <PopoverRoot :open="open" @update:open="setOpen">
    <PopoverTrigger as-child>
      <button
        type="button"
        :disabled="disabled"
        class="inline-flex h-[26px] items-center gap-1.5 rounded-[5px] border bg-background px-2 text-left text-[11px] font-medium hover:bg-muted/50 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary disabled:cursor-default disabled:hover:bg-background"
        :class="[largo ? 'w-full' : 'w-[204px]', open ? 'border-primary ring-2 ring-primary/25' : '']"
        :title="revisao.revisao_motivo || undefined"
      >
        <span v-if="!vazio" class="size-[7px] shrink-0 rounded-full" :class="REVISAO_DOT[revisao.revisao_estado]" />
        <span class="truncate" :class="vazio ? 'font-normal text-muted-foreground' : ''">{{ rotulo.texto }}</span>
        <span v-if="rotulo.detalhe" class="ml-auto truncate text-[10.5px] font-normal text-muted-foreground">{{ rotulo.detalhe }}</span>
        <ChevronDown class="size-3 shrink-0 text-muted-foreground" :class="rotulo.detalhe ? '' : 'ml-auto'" />
      </button>
    </PopoverTrigger>
    <PopoverPortal>
      <PopoverContent
        side="bottom"
        align="center"
        :side-offset="3"
        :collision-padding="8"
        class="z-[70] max-w-[calc(100vw-16px)] rounded-md border bg-background shadow-lg"
        :class="fixando ? 'w-[300px] p-2.5' : 'w-[236px] p-1'"
      >
        <template v-if="!fixando">
          <button
            v-for="o in OPCOES"
            :key="o.tipo"
            type="button"
            class="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs font-medium hover:bg-muted"
            @click="escolher(o.tipo)"
          >
            <span class="size-[7px] shrink-0 rounded-full" :class="o.dot" />
            {{ o.label }}
            <span class="ml-auto text-[10.5px] font-normal text-muted-foreground">{{ o.dica }}</span>
          </button>
          <button type="button" class="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs font-medium hover:bg-muted" @click="abrirFixar">
            <span class="size-[7px] shrink-0 rounded-full" :class="REVISAO_DOT.fixo" />
            Fixar até uma data…
            <span class="ml-auto text-[10.5px] font-normal text-muted-foreground">volta na data</span>
          </button>
          <template v-if="revisao.revisao_tipo">
            <div class="my-1 border-t" />
            <button type="button" class="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs text-muted-foreground hover:bg-muted" @click="escolher(null)">
              Tirar a marca
            </button>
          </template>
        </template>

        <form v-else class="space-y-2" @submit.prevent="fixar">
          <div class="flex items-center gap-1.5 text-xs font-bold">
            <span class="size-[7px] shrink-0 rounded-full" :class="REVISAO_DOT.fixo" />
            Fixar até uma data
          </div>
          <div>
            <div class="mb-0.5 text-[11px] font-semibold text-muted-foreground">Não mexer até</div>
            <div class="flex items-center gap-1">
              <input v-model="ate" type="date" :min="amanha" class="h-8 w-[130px] rounded-md border bg-background px-2 text-xs" />
              <button
                v-for="d in ATALHOS"
                :key="d"
                type="button"
                class="rounded border px-1.5 py-0.5 text-[11px] hover:bg-muted"
                @click="ate = somarDias(hojeISO(), d); erro = ''"
              >+{{ d }}</button>
              <span class="text-[11px] text-muted-foreground">dias</span>
            </div>
          </div>
          <div>
            <div class="mb-0.5 text-[11px] font-semibold text-muted-foreground">Por quê? (volta escrito na data)</div>
            <input v-model="motivo" class="h-8 w-full rounded-md border bg-background px-2 text-xs" placeholder="Shopee falou que paga dia 25/11" @input="erro = ''" />
          </div>
          <div v-if="erro" class="text-[11px] text-red-600 dark:text-red-400">{{ erro }}</div>
          <div class="flex justify-end gap-1.5">
            <button type="button" class="rounded-md border px-2.5 py-1 text-xs font-semibold hover:bg-muted" @click="fixando = false">voltar</button>
            <button type="submit" class="rounded-md bg-primary px-2.5 py-1 text-xs font-semibold text-primary-foreground">fixar{{ ate ? ` até ${diaMes(ate)}` : '' }}</button>
          </div>
        </form>
      </PopoverContent>
    </PopoverPortal>
  </PopoverRoot>
</template>
