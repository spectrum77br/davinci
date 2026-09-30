<script setup lang="ts">
// Liga/desliga (reka Switch). Clicar no rótulo também alterna.
import { useId } from 'vue'
import { SwitchRoot, SwitchThumb } from 'reka-ui'

const props = withDefaults(
  defineProps<{
    modelValue: boolean
    rotulo?: string
    dica?: string
    disabled?: boolean
    id?: string
  }>(),
  { disabled: false },
)

const emit = defineEmits<{ (e: 'update:modelValue', v: boolean): void }>()

const idCampo = props.id ?? `nfse-switch-${useId() ?? ''}`

function mudar(v: boolean | null) {
  emit('update:modelValue', !!v)
}
</script>

<template>
  <div class="inline-flex items-start gap-2">
    <SwitchRoot
      :id="idCampo"
      :model-value="modelValue"
      :disabled="disabled"
      :aria-label="rotulo ? undefined : 'ligar ou desligar'"
      class="relative mt-px inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full bg-muted shadow-inner transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:cursor-not-allowed disabled:opacity-50 data-[state=checked]:bg-primary"
      @update:model-value="mudar"
    >
      <SwitchThumb
        class="block size-4 translate-x-0.5 rounded-full bg-background shadow transition-transform data-[state=checked]:translate-x-[18px]"
      />
    </SwitchRoot>
    <div v-if="rotulo || dica" class="min-w-0">
      <label
        v-if="rotulo"
        :for="idCampo"
        class="block text-sm leading-5"
        :class="disabled ? 'cursor-not-allowed opacity-60' : 'cursor-pointer'"
      >{{ rotulo }}</label>
      <p v-if="dica" class="text-xs text-muted-foreground">{{ dica }}</p>
    </div>
  </div>
</template>
