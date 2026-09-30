<script setup lang="ts">
// Caixa de aviso da Emissão de Serviço, uma cor por tom (com modo escuro).
import { computed, type Component } from 'vue'
import { AlertTriangle, CheckCircle2, Info, XCircle } from 'lucide-vue-next'
import { TOM_AVISO, TOM_TEXTO, type Tom } from '~/lib/nfse'

const props = defineProps<{
  tom: Tom
  titulo?: string
  icone?: Component
  compacto?: boolean
}>()

const PADRAO: Record<Tom, Component> = {
  sucesso: CheckCircle2,
  atencao: AlertTriangle,
  perigo: XCircle,
  info: Info,
  neutro: Info,
}

const icone = computed(() => props.icone ?? PADRAO[props.tom])
// Nos tons claros (info/neutro) só o ícone ganha cor; o texto fica o normal.
const corIcone = computed(() => (props.tom === 'info' || props.tom === 'neutro' ? TOM_TEXTO[props.tom] : ''))
</script>

<template>
  <div
    class="flex gap-2.5 rounded-lg border px-3 py-2.5 text-sm"
    :class="[TOM_AVISO[tom], props.compacto && 'items-center']"
    :role="tom === 'perigo' ? 'alert' : 'status'"
  >
    <component :is="icone" class="size-4 shrink-0" :class="[corIcone, !props.compacto && 'mt-0.5']" aria-hidden="true" />
    <div class="min-w-0 flex-1" :class="props.compacto ? 'flex flex-wrap items-center gap-x-3 gap-y-1' : ''">
      <div class="min-w-0 space-y-1" :class="props.compacto && 'flex-1'">
        <p v-if="titulo" class="font-medium">{{ titulo }}</p>
        <div v-if="$slots.default" class="space-y-1" :class="titulo && 'opacity-90'">
          <slot />
        </div>
      </div>
      <div v-if="$slots.acoes" :class="props.compacto ? 'ml-auto flex shrink-0 flex-wrap gap-2 self-center' : 'mt-2 flex flex-wrap gap-2'">
        <slot name="acoes" />
      </div>
    </div>
  </div>
</template>
